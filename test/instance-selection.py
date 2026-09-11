"""Execute the actual request step with an isolated HTTP transport."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

WORKFLOW = Path(".github/workflows/request-dev.yml").read_text()
STEP = WORKFLOW.split("      - name: Solicitar autorização à Central de Ativações", 1)[1]
SCRIPT = STEP.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
SCRIPT = "\n".join(line[10:] if line.startswith("          ") else line for line in SCRIPT.splitlines())
B = "00000000000000000000000b"
C = "00000000000000000000000c"


class Selection(unittest.TestCase):
    def invoke(self, selected="", command="", status="202", response=None):
        with tempfile.TemporaryDirectory(prefix="r4-publisher-") as directory:
            root = Path(directory)
            (root / "curl").write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ["RUNNER_TEMP"])
oidc = any("oidc.test" in arg for arg in args)
with (root / "calls").open("a") as log:
    log.write("oidc\\n" if oidc else "central\\n")
output = args[args.index("--output") + 1]
pathlib.Path(output).write_text('{"value":"isolated-identity"}' if oidc else os.environ["TEST_RESPONSE"])
if not oidc:
    (root / "payload").write_text(args[args.index("--data") + 1])
    print(os.environ["TEST_STATUS"], end="")
""")
            (root / "curl").chmod(0o755)
            env = {**os.environ, "PATH": directory + os.pathsep + os.environ["PATH"],
                   "RUNNER_TEMP": directory, "TECHNICAL_INSTANCE_ID": selected, "GLOBAL_COMMAND_ID": command,
                   "APP_CODE": "example", "APP_ARCHITECTURE": "{}", "APP_CAPABILITIES": "{}",
                   "APP_FUNCTIONAL_CAPABILITIES": "{}", "ACTIVATION_API_URL": "https://central.test/api",
                   "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.test?request=1",
                   "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "isolated-request", "GITHUB_OUTPUT": str(root / "output"),
                   "GITHUB_STEP_SUMMARY": str(root / "summary"), "GITHUB_REPOSITORY": "example/app",
                   "GITHUB_SHA": "a" * 40, "TEST_STATUS": status,
                   "TEST_RESPONSE": json.dumps(response or {"releaseId": "release-B", "etapa": "autorizada",
                                                            "technicalInstanceId": B, "instanceUid": "23456789abcdefgh"})}
            result = subprocess.run(["bash", "-c", SCRIPT], env=env, text=True, capture_output=True)
            payload = json.loads((root / "payload").read_text()) if (root / "payload").exists() else None
            calls = (root / "calls").read_text().splitlines() if (root / "calls").exists() else []
            output = (root / "output").read_text() if (root / "output").exists() else ""
            return result, payload, calls, output

    def test_selected_id_survives_transport(self):
        result, payload, calls, output = self.invoke(B)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["technicalInstanceId"], B)
        self.assertEqual(calls, ["oidc", "central"])
        self.assertIn("release_id=release-B", output)

    def test_absent_selection_preserves_ordinary_contract(self):
        result, payload, _, _ = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("technicalInstanceId", payload)

    def test_malformed_or_global_selection_fails_before_identity_or_http(self):
        for selected, command in [("invalid", ""), ("$(touch unexpected)", ""), (" ", ""), (B, "global-command")]:
            with self.subTest(selected=selected):
                result, payload, calls, output = self.invoke(selected, command)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])
                self.assertIsNone(payload)
                self.assertEqual(output, "")

    def test_central_denial_never_retries_without_selection(self):
        result, payload, calls, output = self.invoke(B, status="409", response={
            "error": {"code": "COMMERCIAL_INSTANCE_LIFECYCLE_UNSUPPORTED", "message": "blocked"}})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["technicalInstanceId"], B)
        self.assertEqual(calls, ["oidc", "central"])
        self.assertEqual(output, "")

    def test_success_with_foreign_or_missing_identity_is_not_exported(self):
        for response in [{"releaseId": "release-C", "technicalInstanceId": C, "instanceUid": "uid-C"},
                         {"releaseId": "default"}, {"releaseId": "release-B", "technicalInstanceId": B}]:
            result, _, _, output = self.invoke(B, response=response)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main()
