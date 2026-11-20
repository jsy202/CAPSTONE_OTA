"""Build the cluster on Pi2 (native ARM Qt), sign it and install it through the device's own `cluster-update`.

    python -m bench.cluster_install CONFIG --version 4.0.2 --svn 3

Pi2 keeps the previous slot, so a bad image can be left by selecting the old release again. The release is
signed with the key in config["release_dir"]/keys (same key the new cluster device trusts). The check used
is `--initialization-only` (the app's own --check), NOT the Pi3 verifier: this installs, it does not prove
cluster behaviour under load.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import io
import json
import shlex
import subprocess
import tarfile
import threading
from pathlib import Path

from bench import incident_field as field
from capstone_ota.bundle import pack

ROOT = Path(__file__).resolve().parents[1]
CLUSTER_DEVICE = "/opt/capstone-cluster/runs/run-20261120-newcluster/device"
SOURCES = ("app", "build.py", "capstone.qrc", "Cluster.qml", "Main.qml", "main.cpp", "fonts")


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def source_tar() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name in SOURCES:
            archive.add(ROOT / "dashboard/qt_cluster" / name, arcname=name,
                        filter=lambda info: None if "__pycache__" in info.name else info)
    return buffer.getvalue()


def build_on_pi2(pi2, log=print) -> bytes:
    log("[cluster] building on Pi2")
    code = ("set -e; rm -rf ~/cluster-build/dashboard/qt_cluster ~/cluster-build/out ~/cluster-build/pl; "
            "mkdir -p ~/cluster-build/dashboard/qt_cluster; tar -C ~/cluster-build/dashboard/qt_cluster -xf -; "
            "cd ~/cluster-build; python3 dashboard/qt_cluster/build.py --output out >/dev/null; "
            "python3 -c 'from dashboard.qt_cluster.build import payload; payload(\"out/capstone-qt-cluster\", \"pl\")'")
    built = subprocess.run([*pi2.ssh, code], input=source_tar(), capture_output=True, timeout=600)
    if built.returncode:
        raise SystemExit("build on Pi2 failed: " + built.stderr[-600:].decode(errors="replace"))
    payload = subprocess.run([*pi2.ssh, "tar -C ~/cluster-build/pl -cf - ."], capture_output=True, timeout=120, check=True)
    return payload.stdout


def install(config, version, svn, log=print):
    pi2 = field.Board(config["pi2_ssh"])
    staging = ROOT / f".demo/cluster-{version}"
    if staging.exists():
        raise SystemExit(f"{staging} exists; use a new version")
    (staging / "staging").mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(build_on_pi2(pi2, log))) as archive:
        archive.extractall(staging / "staging", filter="data")
    keys = Path(config["release_dir"]) / "keys"
    pack(staging / "staging", staging / "releases/cluster", version, svn, keys / "ivi-private.pem")
    handler = functools.partial(_Quiet, directory=str(staging / "releases"))
    server = http.server.ThreadingHTTPServer((config["laptop_ip"], 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://{config['laptop_ip']}:{server.server_address[1]}/cluster"
    try:
        done = pi2.run(f"cd {field.RUNTIME} && ./root-python -m capstone_ota --root {CLUSTER_DEVICE} cluster-update "
                       f"{shlex.quote(url)} --public-key /home/otauser/cluster-build/trusted.pub "
                       "--initialization-only --systemd", check=False, timeout=300)
    finally:
        server.shutdown()
    answer = json.loads(done.stdout)
    if answer.get("version") != version or answer.get("phase") != "stable":
        raise SystemExit("install did not end stable at " + version + ": " + done.stdout[-300:] + done.stderr[-300:])
    log(f"[cluster] Pi2 cluster is {version} ({answer['result']})")
    return answer


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--svn", type=int, required=True)
    args = parser.parse_args(argv)
    install(json.loads(args.config.read_text()), args.version, args.svn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
