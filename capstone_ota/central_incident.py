"""Pi1 (Central) entry point: run the diagnostic gateway, relay one update to Pi2, and react to the outcome.

If Pi2 answers `recovery_failed`, the incident responder closes the gateway, reports to the control
centre and restores only after a human-approved, signed order. Any other outcome starts no incident.
The gateway keeps answering (as closed or open) for `--linger` seconds so a follow-up probe can be seen.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
import time

from .device import atomic_write
from .gateway import GATEWAY_PORT, DiagnosticGateway, _address
from .incident_responder import IncidentResponder
from .relay import forward


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, help="control centre base URL")
    parser.add_argument("--vehicle", required=True)
    parser.add_argument("--target", required=True, type=Path, help="trusted Pi2 target JSON (same schema as `forward`)")
    parser.add_argument("--release-url", required=True)
    parser.add_argument("--release-public-key", required=True, type=Path)
    parser.add_argument("--order-public-key", required=True, type=Path)
    parser.add_argument("--listen", default=f"0.0.0.0:{GATEWAY_PORT}")
    parser.add_argument("--upstream", required=True, help="Pi2 maintenance service HOST:PORT")
    parser.add_argument("--gateway-log", required=True, type=Path)
    parser.add_argument("--result-out", required=True, type=Path)
    parser.add_argument("--bind", default="127.0.0.1", help="address Pi2 pulls the relayed release from")
    parser.add_argument("--advertise", help="URL host Pi2 should use for that relay (default: --bind)")
    parser.add_argument("--token-file", type=Path, help="bearer token for the control centre")
    parser.add_argument("--decision-timeout", type=float, default=300.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--linger", type=float, default=30.0, help="seconds to keep the gateway up afterwards")
    args = parser.parse_args(argv)
    target = json.loads(args.target.read_text())
    token = args.token_file.read_text().strip() if args.token_file else None
    code = 0
    with DiagnosticGateway(_address(args.listen), _address(args.upstream), args.gateway_log) as gateway:
        threading.Thread(target=gateway.serve_forever, daemon=True).start()
        print(json.dumps({"event": "gateway_listening", "address": list(gateway.server_address)}),
              file=sys.stderr, flush=True)
        responder = IncidentResponder(args.server, args.vehicle, args.order_public_key, args.release_public_key,
                                      gateway, target, decision_timeout=args.decision_timeout,
                                      poll_interval=args.poll_interval, bind=args.bind, advertise=args.advertise,
                                      api_token=token)
        try:
            update = forward(args.release_url, args.release_public_key, target, args.bind, args.advertise)
            response = responder.handle(update)
        except (OSError, ValueError) as exc:
            update, response, code = {"error": str(exc)}, {"action": "none", "reason": str(exc)}, 1
        atomic_write(args.result_out, json.dumps({"update": update, "response": response,
                                                  "gateway": {"open": gateway.is_open,
                                                              "log_tail": gateway.log_tail(20)}}).encode())
        time.sleep(max(0.0, args.linger))
        gateway.shutdown()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
