"""Execute the workflow's actual declaration step without network or credentials."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

source = Path('.github/workflows/request-dev.yml').read_text()
step = source.split('      - name: Validar contrato de publicação', 1)[1].split('      - name:', 1)[0]
script = '\n'.join(line[10:] for line in step.split('        run: |\n', 1)[1].splitlines())
email = {'enabled': True, 'scopePolicy': 'app_only', 'templates': [{'code': 'sample', 'definition': 'emails/sample.json'}]}
cases = [
    ({'core.secrets': {'enabled': True}}, ['core.secrets'], 'single_tenant', 'dedicated', True),
    ({'core.secrets': {'enabled': True}}, ['core.secrets'], 'multi_tenant', 'shared', False),
    ({'core.secrets': {'enabled': True, 'key': 'forbidden'}}, ['core.secrets'], 'single_tenant', 'dedicated', False),
    ({}, [], 'single_tenant', 'dedicated', True),
    ({'core.secrets': {'enabled': True}, 'core.transactional-email': email}, ['core.secrets', 'core.transactional-email'], 'single_tenant', 'dedicated', True),
]
for settings, caps, tenancy, mode, valid in cases:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'oon.deploy.json').write_text(json.dumps({'appCode': 'sample', 'schemaVersion': '1'}))
        (root / 'central.app.json').write_text(json.dumps({'schemaVersion': 2, 'tenancyModel': tenancy, 'deploymentMode': mode, 'capabilities': caps, 'capabilitySettings': settings}))
        output = root / 'output'
        result = subprocess.run(['bash', '-c', script], cwd=directory, env={**os.environ, 'GITHUB_OUTPUT': str(output)}, capture_output=True, text=True)
        assert (result.returncode == 0) == valid, result.stderr
        if valid:
            emitted = dict(line.split('=', 1) for line in output.read_text().splitlines())
            assert json.loads(emitted['functional_capabilities']) == settings
print('5 functional declaration cases passed')

# Execute the exact transport step for all new architectures, without any API.
for availability, tenancy in [('system', 'none'), ('common', 'none'), ('common', 'multi_tenant'), ('self', 'single_tenant'), ('licensed', 'single_tenant'), ('licensed', 'multi_tenant')]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'oon.deploy.json').write_text(json.dumps({'appCode': 'sample', 'schemaVersion': '1'}))
        manifest = {'schemaVersion': 2, 'tenancyModel': tenancy, 'availability': availability, 'capabilities': ['core.secrets'] if availability == 'self' else [], 'capabilitySettings': {'core.secrets': {'enabled': True}} if availability == 'self' else {}}
        (root / 'central.app.json').write_text(json.dumps(manifest))
        output = root / 'output'
        result = subprocess.run(['bash', '-c', script], cwd=directory, env={**os.environ, 'GITHUB_OUTPUT': str(output)}, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        architecture = json.loads(dict(line.split('=', 1) for line in output.read_text().splitlines())['architecture'])
        assert architecture == {'availability': availability, 'tenancyModel': tenancy}
print('6 architecture transport cases passed; canonical acceptance belongs to Central')
