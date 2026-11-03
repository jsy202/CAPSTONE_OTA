# Physical CAN OTA Implementation Plan

> Execute inline with superpowers:executing-plans; separate UI completion delegated
> using dispatching-parallel-agents in the original isolated worktree.

**Goal:** Actual native-CAN Qt service OTA + rollback with reproducible pytest evidence.
**Architecture:** Existing Cluster updater/service/verifier; optional local Unix
observer on two-board bench; third-board native observer preserved.
**Tech:** Python, Qt5 C++/QML, SocketCAN, systemd, pytest, can-utils, tshark, Allure.
**Spec:** ../specs/2026-11-03-physical-can-ota-design.md

## Constraints and review focus
- No app user writes trusted state; no secrets in artifact/report attachments.
- Candidate identity/readiness enforced; old process cannot approve new slot.
- Local observation is not independent third-board or optical measurement.
- Hardware opt-in is explicit; selected failures cannot silently skip.
- Static/global service means hardware tests cannot run concurrently.
- Preserve original Pi1 dashboard; leave final normal V2 service, source and CAN up.

## Tasks
- [ ] 1. Baseline tests; RED/GREEN local observer + bounded service-ready behavior.
- [ ] 2. Deploy separate Pi2 service/display/app user and root-owned device roots;
  dedicated SSH trust; native local observer config; bootstrap repeatable scripts.
- [ ] 3. RED/GREEN hardware pytest wrapper and required config gates; actual physical
  CAN signal/negative/wrap/timeout/unrelated load; normal/mixed/attack OTA cases.
- [ ] 4. Use candump/cangen/canbusload + tcpdump/tshark, attach evidence with TC IDs,
  JUnit, Allure and Python branch coverage; integrate completed progress panel.
- [ ] 5. Fresh full review, fix Important cases via RED/GREEN, full suite; verify
  real hardware and normal final state; update README/runbook/evidence; push main.
