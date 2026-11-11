# OS-level A/B scaffold (not deployed to any board)

This exists as a starting point for the deferred OS-partition OTA work described in
[`docs/OS_LEVEL_AB_DESIGN.md`](../../docs/OS_LEVEL_AB_DESIGN.md). No board has been touched.

- `system.conf.template`: a RAUC `system.conf` skeleton with placeholder partition
  paths. Needs real partition device paths confirmed on the target board before
  it means anything.
- `local-format-test.raucb` / `local-test-ca.cert.pem`: a bundle built and verified
  locally (`rauc bundle`, `rauc info`) to confirm the toolchain works on this laptop.
  Signed with a throwaway self-signed test key — **not a real release artifact**,
  not usable or meaningful on any board, do not treat it as one.

Do not apply anything here to a live board without a verified SD card backup first.
