"""Laptop-only synthetic cockpit preview. No SSH, CAN, OTA installation or /run writes.

Run: python -m demo.local_cockpit_replay
The cluster picture is static; IVI/status/bands use manually assembled test data.
"""
import argparse
import json
from pathlib import Path
import subprocess
import os
import shutil
import time

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.demo/local-cockpit'
COCKPIT = str(RUNTIME / 'cockpit-data')
IVI = str(RUNTIME / 'ivi-data')
IVI_UI = ''

LOG = []
TIMELINE = []
RUN_ID = str(time.time_ns())


def log(now, text):
    LOG.append({"t": time.strftime("%H:%M:%S", time.localtime(now)), "text": text})
    del LOG[:-8]


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def write(ota, pi3, ivi_status, nav, tap, shot_n, ivi_ui):
    now = time.time()
    if ota["stage"] == "metadata" and (not TIMELINE or TIMELINE[-1]["stage"] != "metadata"):
        TIMELINE.clear()
    if not TIMELINE or TIMELINE[-1]["stage"] != ota["stage"]:
        TIMELINE.append({k: ota.get(k) for k in ("stage", "version", "candidate")})
    ota = dict(ota, timeline_id=RUN_ID if TIMELINE[0]["stage"] == "metadata" else None,
               timeline=list(TIMELINE) if TIMELINE[0]["stage"] == "metadata" else None)
    write_json(os.path.join(IVI, "status.json"), ivi_status)
    write_json(os.path.join(IVI, "nav.json"), nav)
    data = {
        "now": now,
        "cluster": {"valid": True, "model_speed": tap["can"]["speed"], "rejected": False, "rev": shot_n},
        "pi3": pi3,
        "ota": ota,
        "ivi": ivi_status,
        "ivi_ui": ivi_ui,
        "shot": shot_n,
        "tap": tap,
        "log": list(LOG),
    }
    write_json(os.path.join(COCKPIT, "data.json"), data)


def keyframe(name, **kw):
    print('Preview:', name, flush=True)
    write(shot_n=None, **kw)
    time.sleep(3.4)


keyframe_with_peak = keyframe

SLOT_V2 = {"version": "2.0.0", "role": "committed"}
SLOT_V3_TRIAL = {"version": "3.0.0", "role": "trial"}
SLOT_EMPTY = {"version": None, "role": "empty"}
SLOT_RECFAIL = {"version": "2.0.0", "role": "recovery_failed"}


def pi3(state, cls, delay, bus_stopped=0.0, fired=None):
    return {"state": state, "cls": cls, "fired": fired or [], "monitored_s": 12.0, "windows": 3,
            "recent_delay_ms": delay, "latency_limit_ms": 100.0, "bus_stopped_s": bus_stopped,
            "last_window": {"lat_p99_ms": delay * 1.1}}


def ivi_status(version, map_active, map_enabled, workers):
    return {"version": version, "map_active": map_active, "workers": workers, "priority": "rr10" if workers else "normal",
            "pid": 4242, "updated": time.time(), "map_enabled": map_enabled, "can": True, "route": map_enabled}


def tap(delay, speed, behind=0):
    return {"delay_ms": delay, "can": {"speed": speed, "rpm": 1800, "gear": 4},
            "cluster": {"speed": max(0, speed - behind * 3), "behind_frames": behind}}


def nav(speed):
    return {"x": 0, "y": 0, "heading": 12.0, "speed": speed, "distance": 120.0, "updated": time.time()}


def replay():
    t0 = time.time()
    log(t0, "IVI running V2")

    # warm-up: let FontLoader/layout settle before any frame is treated as meaningful (avoids a cold-start artifact)
    write(ota={"stage": "committed", "version": "2.0.0", "candidate": None,
               "slot_view": {"A": SLOT_V2, "B": SLOT_EMPTY}, "detail": None},
          pi3=pi3("observing", "normal", 6.1), ivi_status=ivi_status("V2", False, False, 0),
          nav=nav(60), tap=tap(6.1, 60), shot_n=0, ivi_ui=IVI_UI)
    time.sleep(2.5)

    keyframe("normal-driving",
             ota={"stage": "committed", "version": "2.0.0", "candidate": None,
                  "slot_view": {"A": SLOT_V2, "B": SLOT_EMPTY}, "detail": None},
             pi3=pi3("observing", "normal", 6.1), ivi_status=ivi_status("V2", False, False, 0),
             nav=nav(60), tap=tap(6.1, 60), ivi_ui=IVI_UI)

    keyframe_with_peak("update-preparing-v2-to-v3",
             ota={"stage": "metadata", "version": "2.0.0", "candidate": "3.0.0",
                  "slot_view": {"A": SLOT_V2, "B": SLOT_EMPTY}, "detail": None},
             pi3=pi3("observing", "normal", 6.1), ivi_status=ivi_status("V2", False, False, 0),
             nav=nav(60), tap=tap(6.1, 60), ivi_ui=IVI_UI)

    log(time.time(), "Update: downloading and verifying the signed release")
    log(time.time(), "Trial: new version installed, running under probation")

    keyframe_with_peak("v3-trial-map-closed",
             ota={"stage": "verify", "version": "2.0.0", "candidate": "3.0.0",
                  "slot_view": {"A": SLOT_V2, "B": SLOT_V3_TRIAL}, "detail": None},
             pi3=pi3("observing", "normal", 9.8), ivi_status=ivi_status("V3", False, True, 0),
             nav=nav(60), tap=tap(9.8, 60), ivi_ui=IVI_UI)

    keyframe("map-opens-delay-climbing",
             ota={"stage": "verify", "version": "2.0.0", "candidate": "3.0.0",
                  "slot_view": {"A": SLOT_V2, "B": SLOT_V3_TRIAL}, "detail": None},
             pi3=pi3("observing", "normal", 140.0), ivi_status=ivi_status("V3", True, True, 4),
             nav=nav(55), tap=tap(140.0, 55, behind=1), ivi_ui=IVI_UI)

    keyframe("threshold-crossed-suspect",
             ota={"stage": "verify", "version": "2.0.0", "candidate": "3.0.0",
                  "slot_view": {"A": SLOT_V2, "B": SLOT_V3_TRIAL}, "detail": None},
             pi3=pi3("suspect", "freeze", 1800.4, fired=["latency_limit", "latency_p99", "cusum", "freeze_gap"]),
             ivi_status=ivi_status("V3", True, True, 4), nav=nav(0), tap=tap(1800.4, 0, behind=6), ivi_ui=IVI_UI)

    log(time.time(), "Pi3 monitor: confirmed (freeze)")
    keyframe("confirmed-decision",
             ota={"stage": "verify", "version": "2.0.0", "candidate": "3.0.0",
                  "slot_view": {"A": SLOT_V2, "B": SLOT_V3_TRIAL}, "detail": None},
             pi3=pi3("confirmed", "freeze", 1091.4, bus_stopped=0.0,
                     fired=["latency_limit", "latency_p99", "cusum", "freeze_gap"]),
             ivi_status=ivi_status("V3", True, True, 4), nav=nav(0), tap=tap(1091.4, 0, behind=6), ivi_ui=IVI_UI)

    log(time.time(), "Rollback: restoring the previous version")
    keyframe_with_peak("rollback-executing",
             ota={"stage": "recover", "version": "2.0.0", "candidate": "3.0.0",
                  "slot_view": {"A": SLOT_V2, "B": SLOT_V3_TRIAL}, "detail": None},
             pi3=pi3("confirmed", "freeze", 950.0, bus_stopped=12.1), ivi_status=ivi_status("V3", True, True, 4),
             nav=nav(0), tap=tap(950.0, 0, behind=6), ivi_ui=IVI_UI)

    log(time.time(), "Rollback FAILED: needs manual repair")
    keyframe_with_peak("attack-blocked",
             ota={"stage": "recovery_failed", "version": "2.0.0", "candidate": "3.0.0",
                  "slot_view": {"A": SLOT_RECFAIL, "B": SLOT_V3_TRIAL},
                  "detail": "installed tree hash does not match the recorded committed record"},
             pi3=pi3("confirmed", "freeze", 950.0, bus_stopped=12.1), ivi_status=ivi_status("V3", True, True, 4),
             nav=nav(0), tap=tap(950.0, 0, behind=6), ivi_ui=IVI_UI)

    keyframe("attack-blocked-settled",
             ota={"stage": "recovery_failed", "version": "2.0.0", "candidate": "3.0.0",
                  "slot_view": {"A": SLOT_RECFAIL, "B": SLOT_V3_TRIAL},
                  "detail": "installed tree hash does not match the recorded committed record"},
             pi3=pi3("confirmed", "freeze", 950.0, bus_stopped=12.1), ivi_status=ivi_status("V3", True, True, 4),
             nav=nav(0), tap=tap(950.0, 0, behind=6), ivi_ui=IVI_UI)

    print("preview done: preparation -> installed/probation -> rollback check -> recovery refused")


def prepare():
    global IVI_UI
    from PIL import Image, ImageDraw
    runtime = Path(COCKPIT).parent
    for directory in (COCKPIT, IVI):
        Path(directory).mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / 'dashboard/cockpit', runtime / 'cockpit', dirs_exist_ok=True)
    shutil.copytree(ROOT / 'dashboard/ivi/ui', runtime / 'ivi/ui', dirs_exist_ok=True)
    map_file = ROOT / 'dashboard/ivi/map.json'
    if map_file.exists():
        shutil.copy2(map_file, runtime / 'ivi/map.json')
    IVI_UI = (runtime / 'ivi/ui/Ivi.qml').as_uri()
    qml = runtime / 'cockpit/Cockpit.qml'
    text = qml.read_text().replace('file:///run/cockpit/data.json', (Path(COCKPIT) / 'data.json').as_uri())
    text = text.replace('file:///run/cockpit/cluster.bmp', (Path(COCKPIT) / 'cluster.bmp').as_uri())
    text = text.replace('file:///run/capstone-ivi/', Path(IVI).as_uri() + '/')
    text = text.replace('CAPSTONE cockpit', 'CAPSTONE LOCAL PREVIEW')
    text = text.replace('CLUSTER · LIVE', 'CLUSTER · STATIC PREVIEW')
    at = text.rfind('}')
    text = text[:at] + 'Text { x: 500; y: 6; z: 1000; text: "LOCAL PREVIEW · 합성 데이터"; color: "#ffcc33"; font.pixelSize: 24 }\n' + text[at:]
    qml.write_text(text)
    source = ROOT / '.demo/cockpit-board-20261112/live-after-fix.png'
    if source.exists():
        with Image.open(source) as image:
            image = image.crop((0, 0, round(image.width * 2/3), round(image.height * 3/8)))
    else:
        image = Image.new('RGB', (1440, 540), 'black')
        ImageDraw.Draw(image).text((100, 200), 'STATIC CLUSTER PREVIEW: 65 km/h', fill='orange')
    image.save(Path(COCKPIT) / 'cluster.bmp')
    return qml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true', help='prepare isolated files without opening a window')
    args = parser.parse_args()
    qml = prepare()
    if args.prepare_only:
        print(qml)
        return
    executable = shutil.which('qmlscene')
    if not executable:
        raise SystemExit('qmlscene is required for the local preview')
    environment = dict(os.environ, QML_XHR_ALLOW_FILE_READ='1')
    with subprocess.Popen([executable, str(qml)], env=environment) as viewer:
        try:
            replay()
            print('Preview finished. Close the window or press Ctrl+C.', flush=True)
            viewer.wait()
        except KeyboardInterrupt:
            pass
        finally:
            if viewer.poll() is None:
                viewer.terminate()
                viewer.wait(timeout=5)


if __name__ == '__main__':
    main()
