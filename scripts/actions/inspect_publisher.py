"""B4: authenticate only the resolved Actions publisher credential, without writes.

Kept byte-identical in Central and Publisher so neither needs cross-repo secrets.
Only review APIs and an optional namespace GET are reachable on Kubernetes.
"""
import base64
import hashlib
import http.client
import json
import os
import re
import ssl
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, quote

import yaml

ANCHOR = {
    'runId': '35023881435',
    'serverSha256': 'f04d58a42ab3d711b7073420f599d66b902030e19abdded377d66d7824100a8f',
    'caSha256': '8da24d14528a8101737a0ec3d8009c572a8cfdefbed9de08653c1c378e25a637',
    'kubeSystemUid': '55ea77a1-6980-49e6-a6aa-ac19ff4dfb2c',
}
WORKFLOW = '.github/workflows/inspect-publisher-credential.yml'
CONSUMERS = {'oondemand/central-ativacao': 'publish-central-dev.yml',
             'oondemand/oon-publish': 'promote-environment.yml'}
ROOTS = [f'central-ativacao-7k95ghh6gsngebzu-{e}' for e in ('dev', 'hml', 'prod')]
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
SSR = '/apis/authentication.k8s.io/v1/selfsubjectreviews'
SAR = '/apis/authorization.k8s.io/v1/selfsubjectaccessreviews'


class Stop(Exception):
    def __init__(self, code):
        self.code = code


def demand(ok, code):
    if not ok:
        raise Stop(code)


def sha(value):
    return hashlib.sha256(value).hexdigest()


class StrictLoader(yaml.SafeLoader):
    pass


def mapping(loader, node):
    result = {}
    for key, value in node.value:
        k = loader.construct_object(key)
        demand(isinstance(k, str) and k not in result, 'B4_KUBECONFIG_UNSUPPORTED')
        result[k] = loader.construct_object(value)
    return result


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def decode(value):
    demand(isinstance(value, str) and 0 < len(value) <= 131072, 'B4_KUBECONFIG_UNSUPPORTED')
    try:
        # Encoders may wrap lines; whitespace is not part of the credential.
        # Still reject all non-alphabet data rather than using permissive decode.
        return base64.b64decode(re.sub(r'[ \t\r\n\f\v]', '', value), validate=True)
    except Exception:
        raise Stop('B4_KUBECONFIG_UNSUPPORTED') from None


def keys(obj, allowed, required=()):
    demand(isinstance(obj, dict) and set(obj) <= set(allowed) and set(required) <= set(obj),
           'B4_KUBECONFIG_UNSUPPORTED')


def parse_config(encoded, anchor=ANCHOR):
    demand(bool(encoded), 'B4_ACTIONS_CREDENTIAL_MISSING')
    raw = decode(encoded)
    demand(len(raw) <= 65536, 'B4_KUBECONFIG_UNSUPPORTED')
    try:
        # Reject aliases before constructing objects; cap event count/depth.
        depth = 0
        for n, event in enumerate(yaml.parse(raw)):
            demand(n < 500 and not isinstance(event, yaml.AliasEvent), 'B4_KUBECONFIG_UNSUPPORTED')
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                depth += 1
            if isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                depth -= 1
            demand(depth <= 12, 'B4_KUBECONFIG_UNSUPPORTED')
        config = yaml.load(raw, Loader=StrictLoader)
    except Stop:
        raise
    except Exception:
        raise Stop('B4_KUBECONFIG_UNSUPPORTED') from None
    keys(config, ['apiVersion', 'kind', 'preferences', 'clusters', 'contexts', 'users', 'current-context'],
         ['apiVersion', 'kind', 'clusters', 'contexts', 'users', 'current-context'])
    demand(config['apiVersion'] == 'v1' and config['kind'] == 'Config' and
           config.get('preferences', {}) == {}, 'B4_KUBECONFIG_UNSUPPORTED')
    parts = {}
    for plural, singular in [('clusters', 'cluster'), ('contexts', 'context'), ('users', 'user')]:
        entries = config[plural]
        demand(isinstance(entries, list) and len(entries) == 1, 'B4_KUBECONFIG_UNSUPPORTED')
        entry = entries[0]
        keys(entry, ['name', singular], ['name', singular])
        demand(isinstance(entry['name'], str) and bool(entry['name']), 'B4_KUBECONFIG_UNSUPPORTED')
        parts[singular] = entry[singular]
    c, u, ctx = parts['cluster'], parts['user'], parts['context']
    keys(ctx, ['cluster', 'user', 'namespace'], ['cluster', 'user'])
    demand(ctx['cluster'] == config['clusters'][0]['name'] and ctx['user'] == config['users'][0]['name'] and
           config['current-context'] == config['contexts'][0]['name'], 'B4_KUBECONFIG_UNSUPPORTED')
    keys(c, ['server', 'certificate-authority-data'], ['server', 'certificate-authority-data'])
    demand(isinstance(c['server'], str) and re.fullmatch(r'https://[A-Za-z0-9.:-]+/?', c['server']),
           'B4_KUBECONFIG_UNSUPPORTED')
    try:
        url = urlsplit(c['server'])
        demand(url.hostname and not url.username and not url.password and url.path in ('', '/') and
               not url.query and not url.fragment, 'B4_KUBECONFIG_UNSUPPORTED')
        port = url.port or 443
        server = 'https://' + url.hostname.lower() + (f':{port}' if port != 443 else '') + '/'
    except ValueError:
        raise Stop('B4_KUBECONFIG_UNSUPPORTED') from None
    ca = decode(c['certificate-authority-data'])
    demand(sha(server.encode()) == anchor['serverSha256'] and sha(ca) == anchor['caSha256'],
           'B4_CLUSTER_ANCHOR_MISMATCH')
    keys(u, ['token', 'client-certificate-data', 'client-key-data'])
    if set(u) == {'token'}:
        demand(isinstance(u['token'], str) and re.fullmatch(r'[A-Za-z0-9._~-]{1,16384}', u['token']),
               'B4_KUBECONFIG_UNSUPPORTED')
        credential = {'token': u['token'], 'kind': 'inline-token'}
    elif set(u) == {'client-certificate-data', 'client-key-data'}:
        credential = {'cert': decode(u['client-certificate-data']), 'key': decode(u['client-key-data']),
                      'kind': 'inline-client-certificate'}
    else:
        raise Stop('B4_KUBECONFIG_UNSUPPORTED')
    return {'host': url.hostname, 'port': port, 'ca': ca, **credential}


def request(host, port, context, method, path, token=None, payload=None):
    # HTTPSConnection uses neither environment proxies nor automatic redirects.
    conn = http.client.HTTPSConnection(host, port, context=context, timeout=15)
    try:
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json', 'User-Agent': 'oon-b4-inspect'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        conn.request(method, path, body=json.dumps(payload) if payload is not None else None, headers=headers)
        response = conn.getresponse()
        if response.status not in (200, 201):
            return response.status, None
        data = response.read(262145)
        demand(len(data) <= 262144, 'B4_RESPONSE_INVALID')
        return response.status, json.loads(data)
    finally:
        conn.close()


def authorize(env, get):
    repo, run, attempt = (env.get(k, '') for k in ['GITHUB_REPOSITORY', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'])
    commit = env.get('GITHUB_SHA', '')
    demand(repo in CONSUMERS and env.get('GITHUB_REF') == 'refs/heads/main' and
           env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch' and
           env.get('GITHUB_WORKFLOW_REF') == f'{repo}/{WORKFLOW}@refs/heads/main' and
           env.get('B4_WORKFLOW_SHA') == commit and re.fullmatch('[a-f0-9]{40}', commit) and
           re.fullmatch('[0-9]+', run) and re.fullmatch('[0-9]+', attempt), 'B4_TRUSTED_WORKFLOW_REQUIRED')
    execution = get(f'/repos/{repo}/actions/runs/{run}/attempts/{attempt}')
    demand(execution.get('head_sha') == commit and execution.get('head_branch') == 'main' and
           execution.get('event') == 'workflow_dispatch' and execution.get('path') == WORKFLOW and
           str(execution.get('run_attempt')) == attempt and str(execution.get('id')) == run,
           'B4_RUN_IDENTITY_MISMATCH')
    actors = []
    for key, envkey in [('actor', 'GITHUB_ACTOR'), ('triggering_actor', 'GITHUB_TRIGGERING_ACTOR')]:
        actor = execution.get(key) or {}
        login = actor.get('login', '')
        demand(re.fullmatch(r'[A-Za-z0-9-]{1,39}', login) and login == env.get(envkey), 'B4_ACTOR_MISMATCH')
        permission = get(f'/repos/{repo}/collaborators/{quote(login)}/permission')
        demand(permission.get('permission') in ('admin', 'write', 'maintain') and
               (permission.get('user') or {}).get('id') == actor.get('id') and actor.get('id'),
               'B4_WRITER_REQUIRED')
        actors.append({'login': login, 'permission': permission['permission']})
    return {'repository': repo, 'runId': run, 'runAttempt': attempt, 'executorSha': commit,
            'consumerWorkflow': CONSUMERS[repo], 'actors': actors}


def identity(call):
    response = call('POST', SSR, {'apiVersion': 'authentication.k8s.io/v1', 'kind': 'SelfSubjectReview'})
    demand(isinstance(response, dict), 'B4_IDENTITY_INVALID')
    info = (response.get('status') or {}).get('userInfo') or {}
    username, groups = info.get('username'), info.get('groups')
    demand(isinstance(username, str) and isinstance(groups, list) and all(isinstance(g, str) for g in groups),
           'B4_IDENTITY_INVALID')
    match = re.fullmatch(r'system:serviceaccount:([a-z0-9-]{1,63}):([a-z0-9.-]{1,253})', username)
    public = {'source': 'SelfSubjectReview-without-impersonation', 'kind': 'service-account' if match else 'other-redacted',
              'privilegedGroup': 'system:masters' in groups}
    if match:
        public['subject'] = username
        public['rootIdentity'] = match[1] in ROOTS or match[2] == 'root-publisher'
        public['legacyCandidate'] = username == 'system:serviceaccount:oon-system:oon-publisher'
        public['uid'] = info.get('uid') if isinstance(info.get('uid'), str) and UUID.fullmatch(info['uid']) else None
        public['expectedGroupsPresent'] = set(['system:authenticated', 'system:serviceaccounts',
                                                f'system:serviceaccounts:{match[1]}']) <= set(groups)
    return public, (username, info.get('uid'), sorted(groups))


def queries():
    def q(verb, resource, group='', namespace=None, name=None, subresource=None, expectation='deny'):
        attrs = {'verb': verb, 'resource': resource, 'group': group}
        attrs.update({k: v for k, v in [('namespace', namespace), ('name', name), ('subresource', subresource)] if v})
        return attrs, expectation
    result = [q('create', 'namespaces', expectation='observe'),
              q('create', 'clusterroles', 'rbac.authorization.k8s.io'),
              q('create', 'clusterrolebindings', 'rbac.authorization.k8s.io'),
              q('impersonate', 'users'), q('delete', 'nodes'),
              q('impersonate', 'users', name='system:admin'),
              q('impersonate', 'groups', name='system:masters'),
              q('impersonate', 'groups', name='system:serviceaccounts'),
              q('impersonate', 'groups', name='system:serviceaccounts:oon-system'),
              q('get', 'secrets', namespace='oon-system', name='oon-mongo-admin'),
              q('get', 'secrets', namespace='oon-system', name='oon-root-github-app'),
              q('create', 'serviceaccounts', namespace='oon-system', subresource='token')]
    for ns in ROOTS:
        result.extend([q('impersonate', 'serviceaccounts', namespace=ns, name='root-publisher'),
                       q('impersonate', 'groups', name=f'system:serviceaccounts:{ns}'),
                       q('create', 'serviceaccounts', namespace=ns, name='root-publisher', subresource='token'),
                       q('get', 'secrets', namespace=ns, name=ns + '-runtime'),
                       q('create', 'secrets', namespace=ns), q('create', 'pods', namespace=ns),
                       q('create', 'pods', namespace=ns, subresource='exec'),
                       q('patch', 'deployments', 'apps', namespace=ns),
                       q('create', 'rolebindings', 'rbac.authorization.k8s.io', namespace=ns)])
    return result


def inspect(call, report):
    public, first = identity(call)
    report['identity'] = public
    demand(public['kind'] == 'service-account' and not public['privilegedGroup'] and not public['rootIdentity'],
           'B4_FORBIDDEN_IDENTITY')
    demand(public['uid'] and public['expectedGroupsPresent'], 'B4_IDENTITY_INVALID')
    report['checks'] = []
    for attrs, expectation in queries():
        answer = call('POST', SAR, {'apiVersion': 'authorization.k8s.io/v1', 'kind': 'SelfSubjectAccessReview',
                                   'spec': {'resourceAttributes': attrs}})
        status = answer.get('status') or {}
        demand(type(status.get('allowed')) is bool and not status.get('evaluationError') and
               not (status['allowed'] and status.get('denied')), 'B4_AUTHORIZATION_INCONCLUSIVE')
        report['checks'].append({'attributes': attrs, 'expectation': expectation, 'allowed': status['allowed']})
    # UID GET is optional: never grant namespace read solely to make this probe pass.
    obj = call('GET', '/api/v1/namespaces/kube-system', None, allow_forbidden=True)
    if obj is None:
        report['clusterUid'] = 'not-readable'
    else:
        demand((obj.get('metadata') or {}).get('uid') == ANCHOR['kubeSystemUid'], 'B4_CLUSTER_UID_MISMATCH')
        report['clusterUid'] = 'matched'
    _, last = identity(call)
    demand(first == last, 'B4_IDENTITY_CHANGED')
    demand(not any(c['expectation'] == 'deny' and c['allowed'] for c in report['checks']), 'B4_UNSAFE_SCOPE')
    report['status'] = 'credential-inspected-delivery-not-enabled'


def main(env=os.environ):
    report = {'operation': 'inspect', 'mutations': False, 'status': 'blocked-inspection', 'anchor': ANCHOR,
              'secret': 'PUBLISHER_KUBECONFIG_B64', 'secretResolution': 'consumer-repository-without-environment',
              'remaining': ['member-positive-scope-and-environment-isolation', 'dedicated-bindings', 'b5-before-delivery'],
              'limits': ['authorization-sample-not-exhaustive', 'no-database-or-live-delivery-config-proof',
                         'no-rbac-snapshot', 'same-subject-does-not-mean-same-token']}
    try:
        def github_get(path):
            code, data = request('api.github.com', 443, ssl.create_default_context(), 'GET', path,
                                 token=env.get('GH_TOKEN'))
            demand(code == 200 and isinstance(data, dict), 'B4_GITHUB_READ_FAILED')
            return data
        report['execution'] = authorize(env, github_get)
        conf = parse_config(env.get('PUBLISHER_KUBECONFIG_B64'))
        report['credentialFormat'] = conf['kind']
        # Trust only the observed CA. mTLS files exist exclusively in private temp.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cadata=conf['ca'].decode('ascii'))
        with tempfile.TemporaryDirectory(prefix='b4-publisher-') as directory:
            if conf['kind'] == 'inline-client-certificate':
                for name in ('cert', 'key'):
                    file = Path(directory) / name
                    file.write_bytes(conf[name])
                    file.chmod(0o600)
                def no_password():
                    raise Stop('B4_KUBECONFIG_UNSUPPORTED')
                ctx.load_cert_chain(str(Path(directory) / 'cert'), str(Path(directory) / 'key'), password=no_password)
            def kube(method, path, payload=None, allow_forbidden=False):
                demand((method == 'POST' and path in (SSR, SAR)) or
                       (method == 'GET' and path == '/api/v1/namespaces/kube-system'), 'B4_REQUEST_NOT_ALLOWED')
                code, data = request(conf['host'], conf['port'], ctx, method, path, conf.get('token'), payload)
                if code == 403 and allow_forbidden:
                    return None
                demand(code != 401, 'B4_CREDENTIAL_UNAUTHORIZED')
                demand(code != 403, 'B4_REVIEW_FORBIDDEN')
                demand(code != 404, 'B4_API_NOT_FOUND')
                demand(code in (200, 201) and isinstance(data, dict), 'B4_RESPONSE_INVALID')
                return data
            inspect(kube, report)
    except Stop as error:
        report['code'] = error.code
    except (ssl.SSLError, UnicodeError):
        report['code'] = 'B4_TLS_OR_CERTIFICATE_INVALID'
    except (TimeoutError, OSError, http.client.HTTPException):
        report['code'] = 'B4_CONNECTION_FAILED'
    except Exception:
        report['code'] = 'B4_INSPECTION_FAILED'
    report['observedAt'] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'credential-inspected-delivery-not-enabled' else 1


if __name__ == '__main__':
    raise SystemExit(main())
