"""Fixed virtual Cluster. Reconnection does not reset the running receiver."""
import json
import sys

from wire import decode


def main():
    counter, speed = None, None
    for line in sys.stdin:
        sample = decode(json.loads(line))
        accepted = counter is None or sample["counter"] == (counter + 1) % 256
        if accepted:
            counter, speed = sample["counter"], sample["speed"]
        print(json.dumps({"accepted": accepted, "counter": counter, "speed": speed}), flush=True)


if __name__ == "__main__":
    main()
