"""Virtual bench only: real signed OTA plus a separate compatibility oracle.

No fault-injection API is added to the installed OTA. All slot substitution is
confined to this newly created, disposable demo workspace.
"""
import argparse
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import shutil
import sys
import threading

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from capstone_ota.bundle import extract, pack, verify
from capstone_ota.device import Device
from .verify import exchange

HERE = Path(__file__).resolve().parent


def prepare_payload(destination, label, publisher):
    (destination / "bin").mkdir(parents=True)
    shutil.copyfile(HERE / "central_app.py", destination / "bin/app")
    shutil.copyfile(HERE / f"publisher_{publisher}.py", destination / "publisher.py")
    shutil.copyfile(HERE / "wire.py", destination / "wire.py")
    (destination / "release.txt").write_text(label)
    (destination / "bin/app").chmod(0o755)


@contextmanager
def serve(releases):
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(releases)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class VerifiedDevice(Device):
    """Demo adapter only. Ordinary ota CLI and its journal remain unchanged."""
    def __init__(self, root, after_failure=None):
        super().__init__(root)
        self.checks = []
        self.after_failure = after_failure  # Fixture injection, never a production option.

    def _check(self, slot):
        target = self._slot(slot)
        item = {"slot": slot, "payload_label": (target / "release.txt").read_text().strip(), "report": None}
        self.checks.append(item)  # Record any attempted check BEFORE running that payload.
        if not super()._check(slot):
            item["report"] = {"passed": False, "error": "INITIALIZATION_FAILED"}
            return False
        try:
            rows, _pid = exchange([sys.executable, str(HERE / "verify.py"), str(target)], [], timeout=8,
                                  allow_negative_verdict=True)
            if len(rows) != 1 or not isinstance(rows[0], dict) or type(rows[0].get("passed")) is not bool:
                raise ValueError("invalid external verdict")
            report = rows[0]
        except (OSError, ValueError) as exc:
            report = {"passed": False, "error": str(exc)}
        item["report"] = report
        if not report["passed"] and self.after_failure:
            self.after_failure(self, slot, report)
        return report["passed"]


def run(output):
    output = Path(output).absolute()
    output.mkdir(parents=True, exist_ok=False)  # Never overwrite someone else's bench.
    keys = output / "keys"
    keys.mkdir(mode=0o700)
    private = Ed25519PrivateKey.generate()
    key, public = keys / "update.key", keys / "update.pub"
    key.touch(mode=0o600)
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
    public.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM,
                                                       serialization.PublicFormat.SubjectPublicKeyInfo))
    releases = output / "releases"
    for label, version, svn, publisher in [("v1", "1.0.0", 1, "v2"), ("v2", "2.0.0", 2, "v2"),
                                           ("v3", "3.0.0", 2, "v3")]:
        payload = output / "payloads" / label
        prepare_payload(payload, label, publisher)
        pack(payload, releases / label, version, svn, key)
    old = releases / "v1"
    old_release = verify((old / "release.json").read_bytes(), (old / "release.sig").read_bytes(), public)
    if hashlib.sha256((old / "app.tar.gz").read_bytes()).hexdigest() != old_release["sha256"]:
        raise ValueError("old V1 archive hash differs from its signed release")
    with serve(releases) as url:
        baseline = Device(output / "baseline-device")
        baseline.update(url + "/v2", public_key=public)
        baseline_result = baseline.update(url + "/v3")
        normal = VerifiedDevice(output / "normal-device")
        normal.update(url + "/v2", public_key=public)
        normal_result = normal.update(url + "/v3")
        attack_evidence = {"old_signature_valid": True, "old_release": old_release,
                           "trusted_journal_unchanged_during_swap": False}
        def substitute(device, slot, report):
            if (device._slot(slot) / "release.txt").read_text().strip() != "v3":
                return
            if report.get("error") != "RECONNECT_SEQUENCE_INCOMPATIBLE":
                raise ValueError("injection requires an observed real compatibility failure")
            state = device._load()
            target = device._slot(state["committed"]["slot"])
            if not target.is_relative_to(output) or target == device._slot(slot):
                raise ValueError("injection target must be this bench's recovery slot")
            journal = (device.root / "state.json").read_bytes()
            shutil.rmtree(target)
            extract(old / "app.tar.gz", target)
            attack_evidence["trusted_journal_unchanged_during_swap"] = (
                journal == (device.root / "state.json").read_bytes())
        attack = VerifiedDevice(output / "attack-device", after_failure=substitute)
        attack.update(url + "/v2", public_key=public)
        try:
            attack.update(url + "/v3")
            raise ValueError("expected substituted recovery image to be rejected")
        except ValueError as exc:
            if attack.status()["result"] != "recovery_failed":
                raise
            attack_evidence["blocked_reason"] = str(exc.__cause__ or exc)
        attack_evidence.update(status=attack.status(), checks=attack.checks)
    result = {"transport": "stdio CAN-payload simulation; no physical CAN/Qt",
              "baseline": baseline_result, "normal": {"status": normal_result, "checks": normal.checks},
              "attack": attack_evidence}
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description="Virtual reconnect compatibility + signed old-slot substitution")
    parser.add_argument("--output", required=True, type=Path, help="new, disposable output directory")
    args = parser.parse_args()
    try:
        result = run(args.output)
        if (result["baseline"]["result"] != "committed" or result["normal"]["status"]["result"] != "rolled_back"
                or result["attack"]["status"]["result"] != "recovery_failed"):
            raise ValueError("one of the expected outcomes was not reproduced")
        print("Initialization-only: V3 committed (compatibility defect missed)")
        print("External verifier: V3 rejected, V2 recovered")
        print("Signed V1 substituted: recovery refused before V1 check")
        print(f"Evidence: {args.output / 'result.json'}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Demo error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
