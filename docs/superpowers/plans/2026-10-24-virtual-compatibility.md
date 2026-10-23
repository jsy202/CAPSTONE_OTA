# Virtual compatibility demonstration implementation plan

> For agentic workers: use superpowers:executing-plans inline with TDD, then one independent whole-branch review.

**Goal:** Reproduce the agreed reconnect compatibility failure and signed V1 slot substitution in a deterministic virtual bench.

**Architecture:** Demo-only publisher variants, fixed receiver, external verifier, and a harness subclass of the existing Device. No production OTA changes. Real signatures, HTTP downloads, subprocesses and slots; simulated CAN transport.

**Tech Stack:** Existing Python/cryptography/pytest, standard-library subprocess/HTTP/JSON.

**Spec:** docs/superpowers/specs/2026-10-24-virtual-compatibility-design.md

## Global Constraints

- Keep production capstone_ota code and Pi services unchanged.
- Three separate actor roles, no mocks for the compatibility verdict, no forced-failure configuration.
- Explicit modulo-256 contract, initial counter 250, eight initial samples, six post-reconnect samples.
- V1 1.0.0/SVN1; V2 2.0.0/SVN2; V3 3.0.0/SVN2. Signature-valid old slot must never be executed during rejected recovery.
- Output directory must not exist; no keys in served payloads or committed files.
- Basic CLI remains the default; demo is source-checkout only and excluded from the installed package.

## Review Focus

- Common encoder/decoder must not make oracle tautological; verifier expectations are its own literal input/control sequence.
- Missing, malformed, hanging or overly verbose actors must fail rather than be read as a passing test.
- Substitution must happen after a real compatibility failure, change only the recovery slot, and not overwrite trusted state.
- Old V1 must have a valid signature but be rejected before its process/model starts; version history must not be required.
- Existing 45 tests stay green; do not call process/stdio simulation physical CAN, Qt or third-Pi validation.

### Task 1: Implement and test the virtual actors and external verifier

**Files:** demo/wire.py, central_app.py, publisher_v2.py, publisher_v3.py, cluster.py, verify.py; tests/test_virtual_compatibility.py.

**Interfaces:** raw frames are JSON `{id:512,data:<16 hex chars>}` carrying uint16 BE speed, fixed RPM1500/D/warnings0, byte6 counter and byte7 CRC-8/SMBUS. Central stdin receives sample/reconnect messages; stdout frames/reconnect notification. Cluster stdin receives frames; stdout `{accepted,counter,speed}` from applied state. `verify(slot)` returns evidence with init/format/pre/post results. CLI `python demo/verify.py SLOT` exits 0/1 and prints JSON.

- [ ] Write failing tests for real actor subprocesses, normal wrap/reconnect acceptance, V3 rejection despite successful init/format, malformed actor output and verifier timeout.
- [ ] Implement the two actual lifetime-management variants, fixed receiver and bounded verifier. Keep runtime code read-only inside slots.
- [ ] Run actor tests to green; commit.

### Task 2: Connect the virtual verdict to real OTA and demonstrate substitution

**Files:** demo/run.py, virtual demo tests and docs/VIRTUAL_COMPATIBILITY.md; README link.

**Interfaces:** `run(output:Path)` creates an exclusive workspace and returns a report. Demo-only VerifiedDevice._check runs the real init check then external verifier, stores per-check evidence; injector exists only in the demo runner. normal result must be rolled_back to V2; attack result recovery_failed with old image signature valid and no V1 verification/activation. `python -m demo.run --output PATH` prints a short outcome and writes result.json.

- [ ] Write failing full-flow test with real keys/HTTP/A-B and exact expected version/floor/results; assert preserved output on a repeated run and no V1 activation evidence.
- [ ] Implement one bounded runner and clear attack-fixture logs, without modifying production code or adding default configuration fields.
- [ ] Run the full project suite and demo command; independently review the whole branch. Fix important findings via RED/GREEN and the full suite; commit and push the feature branch, not main. Document remaining limits.
