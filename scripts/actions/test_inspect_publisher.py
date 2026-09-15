import base64
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('probe', Path(__file__).with_name('inspect_publisher.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def b64(value):
    return base64.b64encode(value).decode()


def fixture():
    ca = b'public-test-ca'
    config = {'apiVersion': 'v1', 'kind': 'Config', 'current-context': 'publisher',
              'clusters': [{'name': 'cluster', 'cluster': {'server': 'https://cluster.example:6443',
                                                         'certificate-authority-data': b64(ca)}}],
              'contexts': [{'name': 'publisher', 'context': {'cluster': 'cluster', 'user': 'publisher'}}],
              'users': [{'name': 'publisher', 'user': {'token': 'SECRET-TOKEN'}}]}
    anchor = {'serverSha256': p.sha(b'https://cluster.example:6443/'), 'caSha256': p.sha(ca)}
    return config, anchor


class ParseTests(unittest.TestCase):
    def parse(self, config, anchor):
        return p.parse_config(b64(json.dumps(config).encode()), anchor)

    def test_accepts_inline_credential_only_after_matching_anchor(self):
        c, a = fixture()
        self.assertEqual(self.parse(c, a)['kind'], 'inline-token')
        for field in ('serverSha256', 'caSha256'):
            with self.subTest(field=field), self.assertRaisesRegex(p.Stop, '') as error:
                self.parse(c, {**a, field: '0' * 64})
            self.assertEqual(error.exception.code, 'B4_CLUSTER_ANCHOR_MISMATCH')

    def test_external_execution_files_impersonation_proxy_and_mixed_auth_fail(self):
        for target, field, value in [('user', 'exec', {'command': 'touch', 'args': ['/tmp/PWN']}),
                                      ('user', 'auth-provider', {'name': 'oidc'}),
                                      ('user', 'as', 'system:admin'), ('user', 'as-groups', ['system:masters']),
                                      ('user', 'tokenFile', '/tmp/token'), ('user', 'client-key', '/tmp/key'),
                                      ('user', 'client-key-data', b64(b'KEY')),
                                      ('cluster', 'proxy-url', 'https://leak.example'),
                                      ('cluster', 'insecure-skip-tls-verify', True),
                                      ('cluster', 'tls-server-name', 'other.example'),
                                      ('cluster', 'certificate-authority', '/tmp/ca')]:
            c, a = fixture()
            c['users' if target == 'user' else 'clusters'][0][target][field] = value
            with self.subTest(field=field), self.assertRaises(p.Stop):
                self.parse(c, a)

    def test_malformed_yaml_alias_duplicate_key_and_multiple_contexts_fail(self):
        for raw in [b'apiVersion: v1\napiVersion: v1', b'key: &x [foo]\nother: *x',
                    b'!!python/object/apply:os.system ["touch /tmp/PWN"]']:
            with self.assertRaises(p.Stop):
                p.parse_config(b64(raw))
        c, a = fixture()
        c['contexts'].append(copy.deepcopy(c['contexts'][0]))
        with self.assertRaises(p.Stop):
            self.parse(c, a)

    def test_mismatched_references_and_credential_bearing_urls_fail(self):
        for url in ['http://cluster.example', 'https://a:SECRET@cluster.example',
                    'https://cluster.example?token=SECRET', 'https://cluster.example/path',
                    'https://cluster.example:bad', 'https://cluster.example\n']:
            c, a = fixture()
            c['clusters'][0]['cluster']['server'] = url
            with self.assertRaises(p.Stop):
                self.parse(c, a)
        c, a = fixture()
        c['current-context'] = 'other'
        with self.assertRaises(p.Stop):
            self.parse(c, a)


def api_fixture():
    calls = []
    info = {'username': 'system:serviceaccount:oon-system:oon-publisher',
            'uid': '11111111-2222-3333-4444-555555555555',
            'groups': ['system:authenticated', 'system:serviceaccounts', 'system:serviceaccounts:oon-system']}
    state = {'allow': False, 'error': '', 'uidReadable': False}
    def call(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        if path == p.SSR:
            return {'status': {'userInfo': copy.deepcopy(info)}}
        if path == p.SAR:
            return {'status': {'allowed': state['allow'], 'evaluationError': state['error']}}
        if path == '/api/v1/namespaces/kube-system':
            return {'metadata': {'uid': p.ANCHOR['kubeSystemUid']}} if state['uidReadable'] else None
        raise AssertionError('Unapproved request')
    return call, calls, info, state


class IdentityTests(unittest.TestCase):
    def test_authenticates_twice_without_impersonation_and_uid_read_is_optional(self):
        call, calls, info, state = api_fixture()
        report = {}
        p.inspect(call, report)
        self.assertEqual(report['identity']['subject'], info['username'])
        self.assertEqual(report['clusterUid'], 'not-readable')
        self.assertEqual(report['status'], 'credential-inspected-delivery-not-enabled')
        self.assertEqual(len(report['checks']), 26)
        self.assertEqual(sum(path == p.SSR for _, path, _ in calls), 2)
        self.assertTrue(all(method == 'GET' or path in (p.SSR, p.SAR) for method, path, _ in calls))
        self.assertNotIn('user', calls[1][2]['spec'])

    def test_admin_non_sa_and_root_identity_stop_before_authorization(self):
        for variant in ['root', 'admin', 'non-sa']:
            call, calls, info, state = api_fixture()
            if variant == 'root':
                info['username'] = f'system:serviceaccount:{p.ROOTS[0]}:root-publisher'
            elif variant == 'admin':
                info['groups'].append('system:masters')
            else:
                info['username'] = 'PRIVATE-EMAIL'
            report = {}
            with self.assertRaises(p.Stop) as error:
                p.inspect(call, report)
            self.assertEqual(error.exception.code, 'B4_FORBIDDEN_IDENTITY')
            self.assertEqual(len(calls), 1)
            self.assertNotIn('PRIVATE-EMAIL', json.dumps(report))

    def test_unsafe_scope_and_evaluation_failure_cannot_look_successful(self):
        for field, value, code in [('allow', True, 'B4_UNSAFE_SCOPE'),
                                   ('error', 'SECRET-PROVIDER-ERROR', 'B4_AUTHORIZATION_INCONCLUSIVE')]:
            call, _, _, state = api_fixture()
            state[field] = value
            report = {}
            with self.assertRaises(p.Stop) as error:
                p.inspect(call, report)
            self.assertEqual(error.exception.code, code)
            self.assertNotIn('SECRET', json.dumps(report))

    def test_identity_or_cluster_change_blocks_result(self):
        for variant in ('subject', 'cluster'):
            call, calls, info, state = api_fixture()
            def changed(method, path, body=None, **kwargs):
                if variant == 'cluster' and path.endswith('/kube-system'):
                    return {'metadata': {'uid': 'other'}}
                if variant == 'subject' and path == p.SSR and calls:
                    info['uid'] = '22222222-2222-3333-4444-555555555555'
                return call(method, path, body, **kwargs)
            with self.assertRaises(p.Stop):
                p.inspect(changed, {})


class WorkflowTests(unittest.TestCase):
    def fixture(self):
        env = {'GITHUB_REPOSITORY': 'oondemand/central-ativacao', 'GITHUB_REF': 'refs/heads/main',
               'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '2',
               'GITHUB_SHA': 'a' * 40, 'B4_WORKFLOW_SHA': 'a' * 40,
               'GITHUB_ACTOR': 'fanaia', 'GITHUB_TRIGGERING_ACTOR': 'fanaia',
               'GITHUB_WORKFLOW_REF': f'oondemand/central-ativacao/{p.WORKFLOW}@refs/heads/main'}
        run = {'id': 123, 'run_attempt': 2, 'head_sha': 'a' * 40, 'head_branch': 'main',
               'event': 'workflow_dispatch', 'path': p.WORKFLOW,
               'actor': {'login': 'fanaia', 'id': 1}, 'triggering_actor': {'login': 'fanaia', 'id': 1}}
        permission = {'permission': 'admin', 'user': {'id': 1}}
        def get(path):
            return run if '/actions/' in path else permission
        return env, run, permission, get

    def test_only_exact_dispatch_attempt_and_two_writers_are_authorized(self):
        env, run, permission, get = self.fixture()
        self.assertEqual(len(p.authorize(env, get)['actors']), 2)
        for field, value in [('GITHUB_REF', 'refs/heads/feature'), ('B4_WORKFLOW_SHA', 'b' * 40),
                             ('GITHUB_EVENT_NAME', 'pull_request')]:
            with self.assertRaises(p.Stop):
                p.authorize({**env, field: value}, get)
        run['triggering_actor'] = {'login': 'other', 'id': 2}
        with self.assertRaises(p.Stop):
            p.authorize(env, get)
        env, run, permission, get = self.fixture()
        permission['permission'] = 'read'
        with self.assertRaises(p.Stop):
            p.authorize(env, get)

    def test_unknown_exception_sanitized_and_missing_secret_never_contacts_cluster(self):
        env, _, _, _ = self.fixture()
        output = io.StringIO()
        with patch.object(p, 'authorize', return_value={}), patch.object(p, 'request') as network:
            with contextlib.redirect_stdout(output):
                self.assertEqual(p.main(env), 1)
            network.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['code'], 'B4_ACTIONS_CREDENTIAL_MISSING')
        output = io.StringIO()
        with patch.object(p, 'authorize', side_effect=ValueError('SECRET-EXCEPTION')):
            with contextlib.redirect_stdout(output):
                p.main(env)
        self.assertNotIn('SECRET-EXCEPTION', output.getvalue())


class TransportTests(unittest.TestCase):
    def test_real_tls_checks_trust_and_never_follows_redirect_or_proxy(self):
        with tempfile.TemporaryDirectory() as folder:
            cert, key = str(Path(folder) / 'cert.pem'), str(Path(folder) / 'key.pem')
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                            '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
                            '-keyout', key, '-out', cert], check=True, capture_output=True)
            observed = []
            class Handler(BaseHTTPRequestHandler):
                def do_POST(self):
                    self.rfile.read(int(self.headers.get('Content-Length', '0')))
                    observed.append((self.path, self.headers.get('Authorization')))
                    self.send_response(302)
                    self.send_header('Location', 'https://exfiltration.invalid/token')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                def log_message(self, *_):
                    pass
            server = HTTPServer(('127.0.0.1', 0), Handler)
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(cert, key)
            server.socket = server_context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                ctx = ssl.create_default_context(cafile=cert)
                with patch.dict(os.environ, {'HTTPS_PROXY': 'https://exfiltration.invalid'}):
                    code, data = p.request('localhost', server.server_port, ctx, 'POST', p.SSR,
                                           token='TEST-TOKEN', payload={})
                self.assertEqual((code, data), (302, None))
                self.assertEqual(observed, [(p.SSR, 'Bearer TEST-TOKEN')])
                with self.assertRaises(ssl.SSLError):
                    p.request('localhost', server.server_port, ssl.create_default_context(), 'POST', p.SSR,
                              token='MUST-NOT-BE-SENT', payload={})
                self.assertEqual(len(observed), 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
