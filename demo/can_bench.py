"""Three-host bench: CAN data/diagnostics, SSH control, existing signed app OTA."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import secrets
import shlex
import sys
import time

from capstone_ota.bundle import pack
from capstone_ota.device import Device
from .can_link import CanBus, DATA_ID, REPORT, START, VEHICLE, decode_protected, protected_frame
from .run import prepare_payload
from .verify import exchange

SPEEDS = [400, 430, 470, 520, 580, 650]


def handle_cluster_frame(state, bus, identifier, data):
    if identifier == START and len(data) == 8 and data[4:] == b"OTA!":
        state.update(nonce=data[:4], counter=None, speed=None)
        bus.send(REPORT, data[:4] + b"\xff\x00\x00\x02")
    elif identifier == VEHICLE and state["nonce"] is not None and len(data) == 8:
        accepted = False
        try:
            sample = decode_protected({"id": identifier, "data": data.hex()})
            accepted = state["counter"] is None or sample["counter"] == (state["counter"] + 1) % 256
            if accepted:
                state.update(counter=sample["counter"], speed=sample["speed"])
        except ValueError:
            pass
        speed = state["speed"] if state["speed"] is not None else 65535
        bus.send(REPORT, state["nonce"] + bytes([data[6]]) + speed.to_bytes(2, "big") + bytes([accepted]))


def verify_bus(bus, source, timeout=6):
    """The verifier owns inputs and observes both bus frames and Cluster reports."""
    report = {"passed": False, "frames_seen": 0, "accepted": 0, "error": None,
              "transport": "SocketCAN", "expected_speeds": SPEEDS, "observed": []}
    nonce = secrets.token_bytes(4)
    deadline = time.monotonic() + timeout
    try:
        bus.send(START, nonce + b"OTA!")
        while True:
            identifier, data = bus.receive(deadline)
            if identifier == REPORT and data == nonce + b"\xff\x00\x00\x02":
                break
        # Source sends while this host independently receives bus traffic.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(source, list(SPEEDS))
            frames, models = [], []
            while len(frames) < len(SPEEDS) or len(models) < len(SPEEDS):
                identifier, data = bus.receive(deadline)
                if identifier == VEHICLE:
                    frames.append(data)
                elif identifier == REPORT and len(data) == 8 and data[:4] == nonce:
                    if data[7] not in (0, 1):
                        raise ValueError("invalid Cluster diagnostic flag")
                    models.append(data)
                if len(frames) > len(SPEEDS) or len(models) > len(SPEEDS):
                    raise ValueError("unexpected extra sample")
            source_result = pending.result(timeout=max(0.001, deadline - time.monotonic()))
        if source_result != {"sent": len(SPEEDS)}:
            raise ValueError("Central did not complete the requested profile")
        # Wait for the source to finish, then reject trailing/duplicate evidence.
        settling_deadline = min(deadline, time.monotonic() + 0.1)
        while True:
            try:
                identifier, data = bus.receive(settling_deadline)
            except TimeoutError:
                break
            if identifier == VEHICLE or (identifier == REPORT and data[:4] == nonce):
                raise ValueError("unexpected extra sample after profile completion")
        report["frames_seen"] = len(frames)
        compatible = True
        for expected, frame, model in zip(SPEEDS, frames, models):
            accepted = model[7] == 1
            speed = int.from_bytes(model[5:7], "big")
            report["accepted"] += accepted
            report["observed"].append({"input": expected, "counter": frame[6],
                                       "accepted": accepted, "cluster_speed": speed})
            if model[4] != frame[6]:
                raise ValueError("Cluster report does not match the observed frame")
            try:
                decoded = decode_protected({"id": VEHICLE, "data": frame.hex()})
                compatible &= decoded["speed"] == expected and accepted and speed == expected
            except ValueError:
                compatible = False
        report["passed"] = compatible
        if not compatible:
            report["error"] = "E2E_CONFIGURATION_INCOMPATIBLE"
    except (OSError, ValueError, TimeoutError) as exc:
        report["error"] = str(exc)
    return report


def ssh_command(host, checkout, python, arguments):
    if host.startswith("-"):
        raise ValueError("invalid SSH host")
    remote = "cd " + shlex.quote(checkout) + " && " + shlex.join([python, "-m", "demo.can_bench", *arguments])
    return ["ssh", "-oBatchMode=yes", "-oStrictHostKeyChecking=yes", "-oConnectTimeout=5", host, remote]


def send_profile(bus, root, rows):
    if (not isinstance(rows, list) or len(rows) != len(SPEEDS)
            or any(type(speed) is not int or not 0 <= speed <= 3000 for speed in rows)):
        raise ValueError("expected six integer speed inputs")
    app = Path(root).absolute() / "current/bin/app"
    frames, _pid = exchange([str(app)], [{"op": "sample", "speed": speed} for speed in rows])
    if len(frames) != len(rows):
        raise ValueError("invalid candidate output count")
    for frame in frames:
        if not isinstance(frame, dict) or frame.get("id") != VEHICLE:
            raise ValueError("invalid candidate CAN frame")
        bus.send(VEHICLE, bytes.fromhex(frame["data"]))
        time.sleep(0.05)
    return {"sent": len(frames)}


class RemoteDevice(Device):
    """Stationary bench gate. SSH authenticates the separate verifier host."""
    def __init__(self, root, verifier_command):
        super().__init__(root)
        self.verifier_command = verifier_command
        self.reports = []

    def _check(self, slot):
        if not super()._check(slot):
            return False
        try:
            rows, _pid = exchange(self.verifier_command, [], timeout=15, allow_negative_verdict=True)
            if len(rows) != 1 or not isinstance(rows[0], dict) or type(rows[0].get("passed")) is not bool:
                raise ValueError("invalid remote verdict")
            report = rows[0]
        except (OSError, ValueError) as exc:
            report = {"passed": False, "error": str(exc)}
        self.reports.append(report)
        return report["passed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="role", required=True)
    cluster = commands.add_parser("cluster")
    cluster.add_argument("--channel", default="can0")
    source = commands.add_parser("source")
    source.add_argument("--channel", default="can0")
    source.add_argument("--root", required=True, type=Path)
    verifier = commands.add_parser("verify")
    verifier.add_argument("--channel", default="can0")
    verifier.add_argument("--central-host", required=True)
    verifier.add_argument("--central-dir", required=True)
    verifier.add_argument("--central-python", default="python3")
    verifier.add_argument("--central-channel", default="can0")
    verifier.add_argument("--root", required=True)
    update = commands.add_parser("update")
    update.add_argument("--root", type=Path, required=True)
    update.add_argument("--public-key", type=Path)
    update.add_argument("--verifier-host", required=True)
    update.add_argument("--verifier-dir", required=True)
    update.add_argument("--verifier-python", default="python3")
    update.add_argument("--verifier-channel", default="can0")
    update.add_argument("--central-host", required=True)
    update.add_argument("--central-dir", required=True)
    update.add_argument("--central-python", default="python3")
    update.add_argument("--central-channel", default="can0")
    update.add_argument("url", nargs="?")
    package = commands.add_parser("pack")
    package.add_argument("--workspace", type=Path, required=True)
    package.add_argument("--output", type=Path, required=True)
    package.add_argument("--key", type=Path, required=True)
    package.add_argument("--version", required=True)
    package.add_argument("--svn", type=int, required=True)
    package.add_argument("--data-id", type=lambda value: int(value, 0), default=DATA_ID)
    args = parser.parse_args()
    bus = None
    try:
        if args.role == "pack":
            if not 0 <= args.data_id <= 65535:
                raise ValueError("Data ID must be uint16")
            prepare_payload(args.workspace, args.version, "v2")
            (args.workspace / "data-id.txt").write_text(str(args.data_id))
            result = pack(args.workspace, args.output, args.version, args.svn, args.key)
        elif args.role == "update":
            arguments = ["verify", "--channel", args.verifier_channel, "--central-host", args.central_host,
                         "--central-dir", args.central_dir, "--central-python", args.central_python,
                         "--central-channel", args.central_channel, "--root", str(args.root.absolute())]
            command = ssh_command(args.verifier_host, args.verifier_dir, args.verifier_python, arguments)
            device = RemoteDevice(args.root, command)
            result = device.update(args.url, args.public_key)
            result["verification"] = device.reports
        else:
            bus = CanBus(args.channel)
            if args.role == "cluster":
                state = {"nonce": None, "counter": None, "speed": None}
                while True:
                    try:
                        identifier, data = bus.receive(time.monotonic() + 1)
                        handle_cluster_frame(state, bus, identifier, data)
                    except TimeoutError:
                        continue
            elif args.role == "source":
                raw = sys.stdin.buffer.read(4097)
                if len(raw) > 4096:
                    raise ValueError("source input too large")
                rows = json.loads(raw)
                result = send_profile(bus, args.root, rows)
            else:
                command = ssh_command(args.central_host, args.central_dir, args.central_python,
                                      ["source", "--channel", args.central_channel, "--root", args.root])
                def source_profile(speeds):
                    rows, _pid = exchange(command, [speeds], timeout=8)
                    if len(rows) != 1:
                        raise ValueError("invalid source response")
                    return rows[0]
                result = verify_bus(bus, source_profile)
        print(json.dumps(result))
        return 1 if result.get("passed") is False else 2 if result.get("result") in {"rolled_back", "failed"} else 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}))
        return 1
    finally:
        if bus is not None:
            bus.close()


if __name__ == "__main__":
    raise SystemExit(main())
