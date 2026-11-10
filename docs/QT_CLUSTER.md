# Fixed Qt digital cluster

클러스터는 CPU 경합을 관측하는 **고정 앱 한 종류**다. IVI만 V2/V3로 업데이트한다.
클러스터의 버전 배지·버전별 색상·arrival-order 수신 분기·cluster.json은 제거했다.
원본 카운터를 확인하는 수신 방식은 고정이며 CRC, 범위, 중복/과거 카운터, 500ms 신선도 검사를 유지한다.

CAN 0x200을 처리해 Qt 속도 모델을 반영한 직후 0x300 응답을 보낸다.
Pi3는 같은 번호의 입력과 응답 수신 시각을 비교해 지연·끊김을 감시한다.
이는 화면 픽셀 표시 시간이나 악성 앱의 허위 보고를 검증하는 방식이 아니다.
전체 구조와 현재 시나리오는 [README](../README.md), 실보드 CPU 실험은
[결과 문서](IVI_FIELD_MONITOR_RESULTS_2026-11-05.md)를 참고한다.

## Build on PC

Python environment is the existing .venv. System requirements: C++17 compiler,
make, Qt 5.15 development tools/Core/Gui/Quick/QML/Network, QML Quick Controls,
Layouts/Shapes and GraphicalEffects. Xvfb is required for hidden screenshot capture.
There is no Python Qt binding or new pip dependency.

Use a clean source checkout pinned to the commit in
[UPSTREAM.md](../dashboard/qt_cluster/UPSTREAM.md). The current host already has
`/home/jsy/Downloads/Volvo240-DigitalDash`; build.py reads Git's pinned tree and
does not modify that checkout.

```bash
# With system Qt:
.venv/bin/python dashboard/qt_cluster/build.py --source /PATH/Volvo240-DigitalDash

# With this host's separately extracted Qt runtime/development files:
export CAPSTONE_QT_PREFIX=/home/jsy/Downloads/volvo-qt-runtime/root/usr
.venv/bin/python dashboard/qt_cluster/build.py \
  --source /home/jsy/Downloads/Volvo240-DigitalDash --qt-prefix "$CAPSTONE_QT_PREFIX"
```

Build output, imported upstream and binaries are ignored under .demo/qt-build.
Release payload contains bin/app, libexec/dashboard, upstream
license. QML/images are compiled into the executable. Qt libraries stay external
and must match the target architecture. PC x86 binaries must not be installed on Pi.
ARM 실보드 빌드의 과거 결과는 별도 실보드 보고서에 기록되어 있다.

## Local checks

```bash
.venv/bin/python -m pytest -q tests/test_qt_cluster.py tests/test_qt_source.py
```

기본 시험은 숨겨진 Qt 화면을 사용하며 UDP 루프백으로 CAN 전송을 대체한다.
실제 모델/QML 값, 카운터 순환, CRC 거부, 신선도 만료·회복, 관찰자 PID/실행 경로를 확인한다.
새 클러스터에는 버전별 payload 설정을 넣지 않는다:

```python
from dashboard.qt_cluster.build import default_payload
slot = default_payload()
```

`capstone_ota.qt_verify`는 정상 입력 확인용 보조 도구이며 CPU 상시 감시기와 다르다.
현재 CPU 판정은 `capstone_ota.monitor.service`, 시험 운영 감독은 `capstone_ota.probation`이 담당한다.
IVI 공격 시험은 `demo.ivi_attack`으로 실행한다. 기존 `demo.qt_run`, `demo.qt_present`,
`mixed`, `--source buffered`는 제거했다. 과거 실보드 기록은 당시 결과로 보존한다.

실보드 연결 정보는 [THREE_BOARD_SETUP.md](THREE_BOARD_SETUP.md)를 참고한다.
새 소스를 받는 것만으로 기존 보드의 바이너리나 systemd 서비스를 자동 교체하지 않는다.

실보드에는 이번 변경을 배포하지 않았다. 적용 전에 새 소스로 ARM 바이너리를 재빌드해야 한다.
기존 `cluster.json`을 요구하는 옛 ARM 바이너리를 새 고정 payload 생성기에 넣으면 초기화에 실패한다.
하드웨어 설정의 `arm_binary`도 새 바이너리 경로로 맞춘 뒤 시험한다.
