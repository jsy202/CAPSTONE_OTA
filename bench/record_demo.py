"""Run one full incident drill on the real boards and record the Pi2 cockpit screen from start to finish.

    python -m bench.record_demo CONFIG --output DIR [--vulnerable] [--decision approve]

The cockpit runs on Pi2's private virtual display (Xvfb :94). This streams that display to the laptop
(same grabber as bench.remote_view), runs `bench.incident_field run --verdict real --virtual-screen
--map-trigger ui`, and when the run says it waits for the map tap, taps the map tile through XTEST after a
pause so the viewer first sees the V2 screen. The tap is done by this script (recording aid), not by a
person. Output: DIR/video.mp4 (frames timed by their real arrival), DIR/run.log, DIR/run/ (drill evidence).
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

from bench import incident_field as field
from bench import remote_view

MAP_TILE = (1050, 1387)     # the navigation tile in the IVI dock, in virtual-display pixels
FPS = 5.0


BOARD_REC_DIR = "/home/otauser/capstone-incident/rec"


def board_record_command(display: str, path: str, fps: int = 15, width: int = 1080) -> str:
    """Pi2 records its own virtual display in real time (no speed-up). MJPEG (the hardware H.264 encoder stalled under load and
    left black frames), low priority, off core 0:
    the cluster runs on core 0 and its echo latency is what the monitor measures, so recording must not steal it."""
    folder = path.rsplit("/", 1)[0]
    socket = f"/tmp/.X11-unix/X{display.lstrip(':')}"
    return (f"mkdir -p {folder}; until [ -S {socket} ]; do sleep 1; done; "
            f"nice -n 19 taskset -c 1-3 ffmpeg -y -loglevel error -f x11grab -draw_mouse 0 -framerate {fps} "
            f"-video_size 2160x1440 -i {display} -vf scale={width}:-2:flags=fast_bilinear,format=yuv420p "
            f"-c:v mjpeg -q:v 5 {path}")


def screen_ready(line: str) -> bool:
    """The run prints this once it has (re)started Xvfb :94 and the cockpit. Start recording only then: a recorder
    attached to the display before is left on a dead connection when the run recreates it, and films black."""
    return "[screen]" in line and "virtual display" in line and "started" in line


def recording_done(line: str) -> bool:
    """The run prints "[final]" once the drill is over and then shuts the cluster and IVI down; stop filming before that."""
    return line.startswith("[final]")


def board_record_stop_command() -> str:
    return "pkill -INT -x ffmpeg; while pgrep -x ffmpeg >/dev/null; do sleep 1; done; echo stopped"


def transcode(raw: Path, output: Path, ffmpeg="ffmpeg") -> Path:
    """MJPEG from the board -> H.264 mp4 (done on the laptop so Pi2 spends nothing on it)."""
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(raw), "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "23", "-pix_fmt", "yuv420p", str(output)], check=True)
    return output


def fetch_file(pi2, remote: str, local: Path) -> Path:
    with local.open("wb") as handle:
        done = subprocess.run([*pi2.ssh, f"cat {remote}"], stdout=handle, stderr=subprocess.PIPE, timeout=1800)
    if done.returncode or local.stat().st_size == 0:
        raise RuntimeError(f"could not fetch {remote}: {done.stderr[-300:]!r}")
    return local


class RecordingStore(remote_view.FrameStore):
    def __init__(self, directory: Path):
        super().__init__()
        self.directory, self.times = directory, []
        directory.mkdir(parents=True, exist_ok=True)

    def put(self, data, at=None):
        super().put(data, at)
        index = len(self.times)
        (self.directory / f"{index:06d}.jpg").write_bytes(data)
        self.times.append(time.monotonic())


MAX_HOLD = 0.5   # seconds: a frame never stays longer, so waiting/starved stretches are time-lapsed (order is kept)


def build_video(store: RecordingStore, output: Path, ffmpeg="ffmpeg", max_hold=MAX_HOLD) -> Path:
    """Frames in arrival order, each shown until the next one arrived but at most `max_hold` s.

    The real drill takes minutes (trial, detection, rollback) and Pi2 is starved while the map runs, so real-time
    playback would be mostly frozen. The video is therefore time-compressed; frame order is the real order."""
    if len(store.times) < 2:
        raise RuntimeError("fewer than two frames were recorded: the cockpit display never came up")
    lines = ["ffconcat version 1.0"]
    for i, t in enumerate(store.times):
        end = store.times[i + 1] if i + 1 < len(store.times) else t + 1.0 / FPS
        lines += [f"file {store.directory.name}/{i:06d}.jpg", f"duration {min(end - t, max_hold):.4f}"]
    lines.append(f"file {store.directory.name}/{len(store.times) - 1:06d}.jpg")
    listing = store.directory.parent / "frames.ffconcat"
    listing.write_text("\n".join(lines) + "\n")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-vf", "fps=10,scale=1440:-2,format=yuv420p", "-c:v", "libx264", "-crf", "23", str(output)], check=True)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--vulnerable", action="store_true")
    parser.add_argument("--decision", default="approve", choices=["approve", "reject"])
    parser.add_argument("--remote-stream", action="store_true",
                        help="old way: stream frames to the laptop and time-compress (choppy over Wi-Fi); default records on Pi2")
    parser.add_argument("--lead", type=float, default=12.0, help="seconds of V2 screen before the tap")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)
    out = args.output
    if out.exists():
        raise SystemExit(f"{out} exists; use a new output directory")
    out.mkdir(parents=True)
    config = json.loads(args.config.read_text())
    pi2 = field.Board(config["pi2_ssh"])
    store = RecordingStore(out / "frames")
    onboard = not args.remote_stream
    remote_file = f"{BOARD_REC_DIR}/{out.name}.mkv"
    recording = {"on": False, "stopped": False}

    def start_board_recording():
        pi2.run(f"nohup setsid sh -c {shlex.quote(board_record_command(field.VDISPLAY, remote_file))} "
                f"> {BOARD_REC_DIR}.log 2>&1 < /dev/null & echo started", check=False)
        recording["on"] = True
        print("[record] on-board recording started", flush=True)

    if onboard:
        reader = None
    else:
        reader = remote_view.Reader(config["pi2_ssh"], remote_view.grabber_script(fps=FPS, max_width=1440, quality=80), store)
        reader.start()
    command = [sys.executable, "-u", "-m", "bench.incident_field", "run", str(args.config), "--output", str(out / "run"),
               "--verdict", "real", "--virtual-screen", "--map-trigger", "ui", "--decision", args.decision]
    if args.vulnerable:
        command.append("--vulnerable")
    log = (out / "run.log").open("w")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    tapped = {"done": False}

    def tap_later():
        time.sleep(args.lead)
        done = pi2.run(remote_view.click_command(*MAP_TILE, 1), check=False)
        print(f"[record] map tile tapped (exit {done.returncode})", flush=True)
        tapped["done"] = True

    for line in process.stdout:
        log.write(line); log.flush(); print(line, end="")
        if onboard and not recording["on"] and screen_ready(line):
            start_board_recording()
        elif onboard and recording["on"] and not recording["stopped"] and recording_done(line):
            pi2.run(board_record_stop_command(), check=False, timeout=120)
            recording["stopped"] = True
            print("[record] on-board recording stopped at the final state", flush=True)
        if "waiting_for_map_click" in line and not tapped["done"]:
            threading.Thread(target=tap_later, daemon=True).start()
    process.wait()
    time.sleep(4)            # keep recording the final screen
    if onboard:
        if not recording["on"]:
            raise RuntimeError("the cockpit screen never came up, nothing was recorded")
        if not recording["stopped"]:
            pi2.run(board_record_stop_command(), check=False, timeout=120)
        raw = fetch_file(pi2, remote_file, out / "video.raw.mkv")
        video = transcode(raw, out / "video.mp4")
        print(f"[record] on-board recording -> {video} ({video.stat().st_size} bytes, run exit {process.returncode}, tapped={tapped['done']})")
    else:
        reader.stop()
        video = build_video(store, out / "video.mp4")
        print(f"[record] {len(store.times)} frames -> {video} (run exit {process.returncode}, tapped={tapped['done']})")
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
