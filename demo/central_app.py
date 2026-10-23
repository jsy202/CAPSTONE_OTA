#!/usr/bin/env python3
"""Packaged virtual Central: normal init and an actual sample/reconnect path."""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True  # Application slots are immutable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from publisher import Publisher
from wire import encode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    publisher = Publisher()
    label = (Path(__file__).resolve().parents[1] / "release.txt").read_text().strip()
    config = Path(__file__).resolve().parents[1] / "data-id.txt"
    data_id = int(config.read_text()) if config.exists() else None
    if data_id is not None and not 0 <= data_id <= 65535:
        raise ValueError("invalid protection Data ID")
    if args.check:
        print(json.dumps({"initialized": True, "release": label}))
        return
    for line in sys.stdin:
        message = json.loads(line)
        if message["op"] == "reconnect":
            publisher.reconnect()
            result = {"event": "reconnected"}
        elif message["op"] == "sample":
            result = encode(message["speed"], publisher.next_counter(), data_id)
        else:
            raise ValueError("unknown virtual input")
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
