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
    def __init__(self, root):
        super().__init__(root)
        self.checks = []
        self.after_failure = None  # Fixture injection, never a production option.

    def _check(self, slot):
        # Device validates the slot before invoking this single adapter hook.
        target = self.root / "slots" / slot
        item = {"slot": slot, "payload_label": (target / "release.txt").read_text().strip(), "report": None}
        self.checks.append(item)  # Record any attempted check BEFORE running that payload.
        try:
            # The external verifier performs initialization as well as compatibility.
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


def prepare_releases(output):
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
    return releases, public, old_release


def run_baseline(root, url, public):
    device = Device(root)
    device.update(url + "/v2", public_key=public)
    return device.update(url + "/v3")


def run_normal(root, url, public):
    device = VerifiedDevice(root)
    device.update(url + "/v2", public_key=public)
    status = device.update(url + "/v3")
    return {"status": status, "checks": device.checks}


def run_attack(root, url, public, old_archive, old_release):
    evidence = {"old_signature_valid": True, "old_release": old_release,
                "trusted_journal_unchanged_during_swap": False}

    def substitute(device, slot, report):
        candidate = device.root / "slots" / slot
        if (candidate / "release.txt").read_text().strip() != "v3":
            return
        if report.get("error") != "RECONNECT_SEQUENCE_INCOMPATIBLE":
            raise ValueError("injection requires an observed real compatibility failure")
        if recovery_slot not in {"A", "B"}:
            raise ValueError("injection requires an installed V2 recovery slot")
        target = device.root / "slots" / recovery_slot
        if target == candidate or target.is_symlink() or target.parent.is_symlink():
            raise ValueError("injection target must be this bench's recovery slot")
        journal = (device.root / "state.json").read_bytes()
        shutil.rmtree(target)
        extract(old_archive, target)
        evidence["trusted_journal_unchanged_during_swap"] = (
            journal == (device.root / "state.json").read_bytes())

    device = VerifiedDevice(root)
    installed = device.update(url + "/v2", public_key=public)
    recovery_slot = installed["active_slot"]
    device.after_failure = substitute  # Arm only after the known-good install.
    try:
        device.update(url + "/v3")
        raise ValueError("expected substituted recovery image to be rejected")
    except ValueError as exc:
        if device.status()["result"] != "recovery_failed":
            raise
        evidence["blocked_reason"] = str(exc.__cause__ or exc)
    evidence.update(status=device.status(), checks=device.checks)
    return evidence


def run(output):
    output = Path(output).absolute()
    output.mkdir(parents=True, exist_ok=False)  # Never overwrite someone else's bench.
    releases, public, old_release = prepare_releases(output)
    with serve(releases) as url:
        baseline = run_baseline(output / "baseline-device", url, public)
        normal = run_normal(output / "normal-device", url, public)
        attack = run_attack(output / "attack-device", url, public,
                            releases / "v1/app.tar.gz", old_release)
    result = {"transport": "stdio CAN-payload simulation; no physical CAN/Qt",
              "baseline": baseline, "normal": normal, "attack": attack}
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
