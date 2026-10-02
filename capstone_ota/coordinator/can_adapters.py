"""Coordinator-side CAN adapters implementing the core ZoneClient/Probe contracts.

CanBus owns the only receive loop on the SocketCAN transport and fans out
(monotonic receive time, CanFrame) samples to per-call subscribers, so every
call sees only frames received after it subscribed. Kernel envelopes that
cannot form a Classic CAN frame are counted, not delivered; CRC-corrupt
eight-byte frames are delivered raw so verification can count them.
"""
from __future__ import annotations

import queue
import secrets
import threading
import time
from collections import deque
from typing import Callable

from capstone_ota.common.can_protocol import (
    ApplicationState, CanFrame, FunctionalTestRequestFrame, Gear, HeartbeatFrame,
    OtaCommand, OtaCommandFrame, OtaStatus, OtaStatusFrame, Slot,
)
from capstone_ota.common.errors import OtaError
from capstone_ota.common.vehicle_bundle import VehicleBundleManifest

from .compatibility import CompatibilitySnapshot, FrameObservation


class CanBus:
    def __init__(self, transport, *, monotonic: Callable[[], float] = time.monotonic):
        self.transport, self.monotonic = transport, monotonic
        self._subscribers: dict[queue.Queue, frozenset[int]] = {}
        self._lock, self._send_lock = threading.Lock(), threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.failure: BaseException | None = None
        self.malformed_frames = 0

    def start(self) -> None:
        self.transport.open()
        self._thread = threading.Thread(target=self._receive, name="can-receive", daemon=True)
        self._thread.start()

    def _receive(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    frame = self.transport.recv(0.2)
                except ValueError:
                    self.malformed_frames += 1
                    continue
                if frame is None:
                    continue
                sample = (self.monotonic(), frame)
                with self._lock:
                    receivers = [q for q, ids in self._subscribers.items() if frame.can_id in ids]
                for receiver in receivers:
                    receiver.put(sample)
        except BaseException as exc:
            if not self._stop.is_set():
                self.failure = exc

    def subscribe(self, ids) -> queue.Queue:
        receiver: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers[receiver] = frozenset(ids)
        return receiver

    def unsubscribe(self, receiver: queue.Queue) -> None:
        with self._lock:
            self._subscribers.pop(receiver, None)

    def send(self, frame: CanFrame, timeout_s: float = 1.0) -> None:
        self.check()
        with self._send_lock:
            self.transport.send(frame)

    def check(self) -> None:
        if self.failure is not None:
            raise OtaError("CAN_BUS_FAILED", f"CAN receive loop stopped: {self.failure}")
        if self._thread is not None and not self._thread.is_alive() and not self._stop.is_set():
            raise OtaError("CAN_BUS_FAILED", "CAN receive loop stopped")

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)
        self.transport.close()


_PENDING = {OtaStatus.PREPARING, OtaStatus.READY, OtaStatus.ACTIVATING, OtaStatus.VERIFYING, OtaStatus.ROLLING_BACK}
_STALE_DETAIL, _TOKEN_DETAIL = 2, 3


def _expected_cluster(state) -> tuple[tuple[int, int, int], tuple[int, int]]:
    if not state.stable_bundle:
        raise OtaError("RECOVERY_BASELINE_UNKNOWN", "stable Cluster version is unknown")
    target = next((t for t in state.stable_bundle["targets"] if t["ecu_id"] == "digital-cluster"), None)
    if target is None:
        raise OtaError("RECOVERY_BASELINE_UNKNOWN", "stable bundle lacks digital-cluster")
    try:
        version = tuple(int(p) for p in target["software_version"].split("."))
    except ValueError as exc:
        raise OtaError("VERSION_NOT_WIRE_REPRESENTABLE", "stable Cluster version is not numeric") from exc
    return version, (target["protocol_major"], target["protocol_minor"])


class CanZoneClient:
    """Synchronous ZoneClient over 0x600/0x601 with QUERY-based resynchronization.

    A lost response is never answered by blindly repeating a mutation: the
    client asks QUERY_STATUS and resends the mutation only when the serialized
    agent reports the pre-command state. Repeated status counters are ignored.
    """
    _PRE_STATE = {OtaCommand.PREPARE: {OtaStatus.IDLE}, OtaCommand.ACTIVATE: {OtaStatus.READY},
                  OtaCommand.COMMIT: {OtaStatus.VERIFYING},
                  OtaCommand.ROLLBACK: {OtaStatus.READY, OtaStatus.VERIFYING}}

    def __init__(self, bus, state_provider: Callable, *, monotonic: Callable[[], float] = time.monotonic,
                 retry_s: float = 1.0):
        self.bus, self.state_provider, self.monotonic, self.retry_s = bus, state_provider, monotonic, retry_s
        self._counter = 0
        self._last_status_counter: int | None = None

    def _send(self, command: OtaCommand, token: int, slot: Slot) -> None:
        self.bus.send(OtaCommandFrame(command, token, slot, 0, self._counter).encode())
        self._counter = (self._counter + 1) % 16

    def _receive(self, receiver: queue.Queue, deadline: float, sent_at: float):
        """Return (receive time, frame) or None when this retry window expires."""
        wait = min(deadline - self.monotonic(), self.retry_s)
        if wait <= 0:
            return None
        try:
            received_at, frame = receiver.get(timeout=wait)
        except queue.Empty:
            return None
        return (received_at, frame) if received_at >= sent_at else (None, None)

    def _exchange(self, command: OtaCommand, token: int, slot: Slot, expected: set[OtaStatus],
                  timeout_s: float, *, heartbeat: bool = False) -> OtaStatusFrame:
        deadline = self.monotonic() + timeout_s
        receiver = self.bus.subscribe({0x601, 0x101} if heartbeat else {0x601})
        try:
            sent_at = self.monotonic()
            outstanding = deque([command])
            self._send(command, token, slot)
            stale_resyncs = 0
            status, stable_heartbeat = None, not heartbeat
            while self.monotonic() < deadline:
                item = self._receive(receiver, deadline, sent_at)
                if item is None:
                    outstanding.append(OtaCommand.QUERY_STATUS)
                    self._send(OtaCommand.QUERY_STATUS, token, slot)
                    continue
                received_at, frame = item
                if frame is None:
                    continue
                if frame.can_id == 0x101:
                    try:
                        beat = HeartbeatFrame.decode(frame)
                    except ValueError:
                        continue
                    version, protocol = _expected_cluster(self.state_provider())
                    if (beat.state != ApplicationState.STABLE or beat.software_version != version
                            or (beat.protocol_major, beat.protocol_minor) != protocol):
                        raise OtaError("ZONE_NOT_STABLE", "Cluster heartbeat is not the stable version set")
                    stable_heartbeat = True
                else:
                    try:
                        decoded = OtaStatusFrame.decode(frame)
                    except ValueError:
                        continue
                    if decoded.transaction_token != token or decoded.counter == self._last_status_counter:
                        continue
                    self._last_status_counter = decoded.counter
                    answered = outstanding.popleft() if outstanding else OtaCommand.QUERY_STATUS
                    if decoded.status == OtaStatus.ERROR and decoded.detail == _STALE_DETAIL and stale_resyncs < 16:
                        # Agent kept a counter session from a previous coordinator
                        # process; advance until the next contiguous counter.
                        stale_resyncs += 1
                        outstanding.append(command)
                        self._send(command, token, slot)
                        continue
                    if decoded.status in expected and decoded.detail == 0:
                        status = decoded
                    elif decoded.status in {OtaStatus.ERROR, OtaStatus.RECOVERY_FAILED}:
                        if (answered == OtaCommand.QUERY_STATUS and command == OtaCommand.PREPARE
                                and decoded.detail == _TOKEN_DETAIL):
                            outstanding.append(command)  # Agent never saw PREPARE.
                            self._send(command, token, slot)
                            continue
                        raise OtaError("ZONE_REJECTED", f"Cluster replied {decoded.status.name} detail {decoded.detail}")
                    elif answered == OtaCommand.QUERY_STATUS and decoded.status in self._PRE_STATE.get(command, ()):
                        outstanding.append(command)
                        self._send(command, token, slot)
                        continue
                    elif command == OtaCommand.QUERY_STATUS:
                        raise OtaError("ZONE_NOT_STABLE", f"Cluster reports {decoded.status.name}")
                if status is not None and stable_heartbeat:
                    return status
            raise OtaError("ZONE_TIMEOUT", f"Cluster did not confirm {command.name}")
        finally:
            self.bus.unsubscribe(receiver)

    def status(self, transaction_id: str | None, token: int, timeout_s: float) -> OtaStatusFrame:
        if transaction_id is not None:
            return self._exchange(OtaCommand.QUERY_STATUS, token, Slot.A, set(OtaStatus), timeout_s)
        deadline = self.monotonic() + timeout_s
        result = self._exchange(OtaCommand.QUERY_STATUS, 0, Slot.A, {OtaStatus.IDLE}, timeout_s, heartbeat=True)
        state = self.state_provider()
        cluster = (state.ecu_states or {}).get("digital-cluster", {})
        if (state.transaction_id is not None and cluster.get("prepare") in {"intent", "done"}
                and cluster.get("rollback") != "done"):
            # Token 0 cannot reveal a staged slot; reconcile the owned UUID too.
            owned = int(state.transaction_id[:8], 16)
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise OtaError("ZONE_TIMEOUT", "no budget to reconcile owned transaction")
            current = self._exchange(OtaCommand.QUERY_STATUS, owned, Slot.A, set(OtaStatus), remaining)
            if current.status in _PENDING or current.status in {OtaStatus.ERROR, OtaStatus.RECOVERY_FAILED}:
                raise OtaError("ZONE_NOT_STABLE", "Cluster still holds the owned transaction")
        return result

    def prepare(self, transaction_id, token, slot, timeout_s):
        return self._exchange(OtaCommand.PREPARE, token, slot, {OtaStatus.READY}, timeout_s)

    def activate(self, transaction_id, token, slot, timeout_s):
        return self._exchange(OtaCommand.ACTIVATE, token, slot, {OtaStatus.VERIFYING}, timeout_s)

    def commit(self, transaction_id, token, slot, timeout_s):
        return self._exchange(OtaCommand.COMMIT, token, slot, {OtaStatus.COMMITTED}, timeout_s)

    def rollback(self, transaction_id, token, slot, timeout_s):
        deadline = self.monotonic() + timeout_s
        try:
            return self._exchange(OtaCommand.ROLLBACK, token, slot, {OtaStatus.ROLLED_BACK}, timeout_s)
        except OtaError as exc:
            if exc.code != "ZONE_REJECTED" or f"detail {_TOKEN_DETAIL}" not in exc.message:
                raise
        # Cluster never staged this UUID. Acknowledge only after a fresh stable
        # discovery proves no pending slot; a timeout never reaches this point.
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            raise OtaError("ZONE_TIMEOUT", "no budget to confirm stable Cluster")
        stable = self._exchange(OtaCommand.QUERY_STATUS, 0, Slot.A, {OtaStatus.IDLE}, remaining, heartbeat=True)
        return OtaStatusFrame(OtaStatus.ROLLED_BACK, token, stable.slot, 0, stable.counter)


class CanCompatibilityProbe:
    """Collect one bounded observation window of real bus traffic.

    Central process health comes from the injected service check, polled during
    the window. Cluster process health is inferred only from heartbeat gaps;
    the validator independently re-checks heartbeat freshness and content.
    """
    def __init__(self, bus, central_running: Callable[[], bool], *, window_s: float = 5.0,
                 min_window_s: float = 1.0, monotonic: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.bus, self.central_running = bus, central_running
        self.window_s, self.min_window_s = window_s, min_window_s
        self.monotonic, self.sleep = monotonic, sleep

    def collect(self, bundle: VehicleBundleManifest, *, trial: bool, not_before: float,
                timeout_s: float) -> CompatibilitySnapshot:
        window = min(self.window_s, timeout_s - 0.5)
        if window < self.min_window_s:
            raise OtaError("ACTION_TIMEOUT", "verification budget is shorter than the minimum window")
        receiver = self.bus.subscribe({0x100, 0x101, 0x200, 0x611})
        try:
            started = max(self.monotonic(), not_before)
            end = started + window
            first = secrets.randbelow(255)
            requests = (FunctionalTestRequestFrame(650, 3000, Gear.DRIVE, 1, first),
                        FunctionalTestRequestFrame(0, 800, Gear.PARK, 0, (first + 1) % 256))
            healthy, frames, results, sent = True, [], [], 0
            last_cluster = started
            cluster_healthy = True
            while (now := self.monotonic()) < end:
                if sent < len(requests) and now >= started + 0.25 + sent * window / 2:
                    self.bus.send(requests[sent].encode())
                    sent += 1
                healthy = healthy and bool(self.central_running())
                deadline = min(end, now + 0.25)
                while (remaining := deadline - self.monotonic()) > 0:
                    try:
                        observed_at, frame = receiver.get(timeout=remaining)
                    except queue.Empty:
                        break
                    if observed_at < started:
                        continue
                    sample = FrameObservation(observed_at, frame.can_id, frame.data)
                    if frame.can_id == 0x611:
                        results.append(sample)
                    else:
                        frames.append(sample)
                    if frame.can_id == 0x101:
                        cluster_healthy &= observed_at - last_cluster <= bundle.health_policy.heartbeat_timeout_s
                        last_cluster = observed_at
            ended = self.monotonic()
            cluster_healthy &= ended - last_cluster <= bundle.health_policy.heartbeat_timeout_s
            return CompatibilitySnapshot(started, ended,
                                         {"central-control": healthy, "digital-cluster": cluster_healthy},
                                         tuple(frames), requests[:sent], tuple(results))
        finally:
            self.bus.unsubscribe(receiver)
