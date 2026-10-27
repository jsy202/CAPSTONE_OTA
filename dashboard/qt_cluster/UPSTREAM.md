# Volvo Qt integration

Source: https://github.com/whitfijs-jw/Volvo240-DigitalDash
Pinned commit: 793452919127065536bcb7a08f98838fa963d75e
License: MIT, retained in LICENSE.upstream and every payload.

build.py imports only the pinned app tree into ignored .demo/qt-build/upstream.
It compiles the original eight gauge model sources and QML/resources with our
main.cpp entry point. It does not initialize GPIO/GPS/sysfs, start the original
desktop simulator or require Eigen/buildroot. Original Downloads remain untouched.

The imported QML copy has a neutral startup caption/title, no two-second joke
screen, a readiness property, an object name on the actual speed Gauge item and
two explicit Loader binding targets. The adapter waits for async QML incubation
and gauge visibility before reporting ready. These are tracked import transformations
in build.py; other upstream layouts/resources remain unchanged.

The actual speed Gauge.value and C++ SpeedometerModel.currentValue are read
separately. Gear/validity are added by Overlay.qml. Original warning/RPM models
are reused. This is model/binding verification, not an independent optical measurement.
