#!/usr/bin/env bash
# Build and run the Qt unit (AppStatus) and QML component (StatusBadge) tests.
# usage: run-qt-tests.sh <qt-prefix, e.g. ~/Qt/5.15.2/gcc_64>
set -euo pipefail

if [[ $# -ne 1 || ! -x $1/bin/qmake ]]; then
  echo "usage: $0 <qt-prefix containing bin/qmake>" >&2
  exit 2
fi
qt=$(cd -- "$1" && pwd)
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
build=$(mktemp -d)
trap 'rm -rf -- "$build"' EXIT
export LD_LIBRARY_PATH="$qt/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-offscreen}

build_test() {   # build_test <dir> <pro> [qmake args...]
  mkdir -p "$build/$1"
  (cd "$build/$1" && "$qt/bin/qmake" "$here/$2" "${@:3}" >/dev/null && make -s -j"$(nproc)" >/dev/null)
}
build_test status app_status_test.pro
build_test signals cluster_signals_test.pro
build_test signals-fault cluster_signals_test.pro CAPSTONE_FAULT_SPEED_DIVISOR=10
build_test ipc cluster_ipc_server_test.pro
"$build/status/app_status_test"
"$build/signals/cluster_signals_test"
"$build/signals-fault/cluster_signals_fault_test"
"$build/ipc/cluster_ipc_server_test"
"$qt/bin/qmllint" "$here/../overlay/app/StatusBadge.qml"
(cd "$here" && "$qt/bin/qmltestrunner" -input tst_status_badge.qml)
