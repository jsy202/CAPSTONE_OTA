"""Move the demo boards to a new IVI baseline (V2 = 2.1.0) with an ordinary signed OTA update.

    python -m bench.ivi_rebaseline build   CONFIG --output NEW_DIR   # signed releases/v2 (2.1.0) + releases/v3 (3.0.0)
    python -m bench.ivi_rebaseline install CONFIG --output NEW_DIR   # Pi2: 2.0.0 -> 2.1.0 through `ivi-update`

NEW_DIR keeps the layout of the old release dir (releases/v2, releases/v3, keys/), so afterwards
config "release_dir" can point at it and "baseline_version" can be set to 2.1.0. The private key is
never copied: keys/ivi-private.pem in NEW_DIR is a symlink to the old one (bench.incident_field signs
its attack image through that path).
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import shlex
import shutil
import sys
import threading
from pathlib import Path

from bench import incident_field as field
from bench.ivi_release import RELEASES
from capstone_ota.bundle import pack, version_key
from dashboard.ivi.build import payload

BASELINE_VERSION = "2.1.0"
# Same labels/svn as bench.ivi_release.RELEASES; only V2's version changes (the device refuses a repeat of 2.0.0).
NEW_RELEASES = {name: (label, BASELINE_VERSION if name == "v2" else version, svn)
                for name, (label, version, svn) in RELEASES.items()}


def releases_for(v2_version=BASELINE_VERSION, v2_svn=None, v3_version=None, v3_svn=None):
    """Release table for a new baseline. A committed V3 raises the device's svn floor, so a later baseline
    must carry a higher svn (and a higher version) than everything the device has already committed."""
    table = dict(NEW_RELEASES)
    table["v2"] = ("V2", v2_version, v2_svn if v2_svn is not None else table["v2"][2])
    if v3_version is not None:
        table["v3"] = ("V3", v3_version, v3_svn if v3_svn is not None else table["v3"][2])
    return table


def build_releases(release_dir, output_dir, map_file=None, table=None):
    """Sign fresh releases from the current dashboard/ivi build with the key pair already in release_dir/keys."""
    release_dir, output_dir = Path(release_dir).absolute(), Path(output_dir).absolute()
    private, public = release_dir / "keys/ivi-private.pem", release_dir / "keys/ivi-public.pem"
    if not private.is_file() or not public.is_file():
        raise ValueError(f"{release_dir}/keys needs ivi-private.pem and ivi-public.pem")
    if output_dir.exists():
        raise ValueError(f"{output_dir} exists; choose a new output directory")
    keys = output_dir / "keys"
    keys.mkdir(parents=True)
    shutil.copyfile(public, keys / "ivi-public.pem")
    (keys / "ivi-private.pem").symlink_to(private.resolve())  # a pointer, never a copy of the secret
    releases = {}
    for name, (label, version, svn) in (table or NEW_RELEASES).items():
        staging = output_dir / "payloads" / name
        payload(staging, label, map_file=map_file)
        releases[name] = pack(staging, output_dir / "releases" / name, version, svn, private)
    return {"output": str(output_dir), "public_key": str(keys / "ivi-public.pem"), "releases": releases}


def update_command(url):
    """Pi2's own ivi-update, written like the orchestrator's other Pi2 calls (pinned trusted.pub, passing recheck)."""
    return (f"cd {field.RUNTIME} && ./root-python -m capstone_ota --root {field.IVI_ROOT} ivi-update {shlex.quote(url)} "
            f"--check-command {shlex.quote(json.dumps(field.passing_recheck()))}")


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def install_baseline(config, boards, output_dir, log=print):
    pi2 = boards["pi2"]
    output_dir = Path(output_dir).absolute()
    release = output_dir / "releases/v2"
    wanted = json.loads((release / "release.json").read_text())["version"]
    before = field.pi2_status(pi2)
    if before.get("phase") != "stable":
        raise SystemExit("Pi2 is not stable, refusing to update: " + json.dumps(before))
    if before.get("version") == wanted:
        log(f"[rebaseline] Pi2 is already at {wanted}; nothing to do")
        field.check_committed_slot(pi2)
        return {"skipped": True, "version": wanted, "phase": before["phase"], "before": before}
    if version_key(before["version"]) >= version_key(wanted):
        raise SystemExit(f"Pi2 is at {before['version']}, not older than {wanted}; the device would refuse the update")
    field.check_committed_slot(pi2)
    handler = functools.partial(_Quiet, directory=str(output_dir / "releases"))
    server = http.server.ThreadingHTTPServer((config["laptop_ip"], 0), handler)
    url = f"http://{config['laptop_ip']}:{server.server_address[1]}/v2"
    log(f"[rebaseline] about to update Pi2 {before['version']} -> {wanted} (signed OTA, release served from {release})")
    log(f"[rebaseline] serving {url}; running on Pi2: {update_command(url)}")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        done = pi2.run(update_command(url), check=False, timeout=600)  # exit 2 can mean rolled_back, so judge the answer
        try:
            answer = json.loads(done.stdout)
        except ValueError:
            answer = {"raw": (done.stdout + done.stderr)[-400:]}
    finally:
        server.shutdown()
        server.server_close()
    field.check_committed_slot(pi2)
    after = field.pi2_status(pi2)
    if after.get("version") != wanted or after.get("phase") != "stable":
        raise RuntimeError(f"Pi2 did not end at stable {wanted}: status={json.dumps(after)} update={json.dumps(answer)}")
    log(f"[rebaseline] Pi2 committed {wanted} (stable)")
    return {"skipped": False, "version": after["version"], "phase": after["phase"], "before": before, "update": answer}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["build", "install"])
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--map", type=Path, help="map.json to ship inside the V3 release (build only)")
    for flag in ("v2-version", "v3-version"):
        parser.add_argument("--" + flag, help="build only: version of this release (default 2.1.0 / 3.0.0)")
    for flag in ("v2-svn", "v3-svn"):
        parser.add_argument("--" + flag, type=int, help="build only: security version (must exceed the device's current floor)")
    args = parser.parse_args(argv)
    config, boards = field.load(args.config)
    if args.command == "build":
        print(json.dumps(build_releases(config["release_dir"], args.output, args.map,
                                          releases_for(args.v2_version or BASELINE_VERSION, args.v2_svn, args.v3_version, args.v3_svn)), indent=1))
    else:
        print(json.dumps(install_baseline(config, boards, args.output), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
