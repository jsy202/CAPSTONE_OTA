# Historical implementation — not the default OTA

The original MQTT/HTTPS, multi-ECU coordinator, zone agent, CAN contracts,
Qt packaging scripts, configuration, documentation and tests are preserved
here as source reference. They are not imported or installed by the new default
package. Nothing was removed from existing running Raspberry Pi services.

The original directory layout is available at Git commit `dca36a8` (the parent
baseline before simplification). Use that historical checkout and its own
dependencies to reproduce the original 651-test suite and deployment scripts;
the archived installation paths and console entry points do not describe the
new default package. The historical README is retained verbatim in this folder.

Uncommitted work in other local checkouts was not copied, committed, or deleted.

The new workflow is described in the repository's root README.
