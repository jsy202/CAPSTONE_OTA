# 콕핏 OTA 표시 순서 수정

## 화면의 의미

공격 시연의 표시 순서는 다음과 같다.

1. 업데이트 준비 — 다운로드·검증·설치 시작.
2. 설치 완료 · V2 → V3 · 시험 운전 — 새 앱의 기동 검사 후 외부 검증 진입.
3. 롤백 준비 · 복구 이미지 검사 — 이전 이미지로 돌아가기 전 검사.
4. 복구 이미지 거부 · 실행 차단 — 복구 이미지 검사 실패.

‘설치 완료’는 최종 승인이 아니다. 정상 업데이트는 외부 검증 통과 뒤
‘업데이트 최종 승인’으로 끝난다. 정상 롤백은 ‘이전 버전 복귀 완료’로 끝난다.
복구 이미지 검사는 통과했지만 기동·재검증이 실패한 경우는 보안 차단으로
오인하지 않도록 ‘복구 실패 · 수동 점검 필요’로 표시한다.

## 원인과 수정

- 기존 표시가 설치 전 준비를 `trial`에, 완료를 `committed`에 연결해 의미가 섞였다.
- `onDChanged`에서 파생 바인딩 `ota`가 갱신되기 전 값을 참조할 수 있었다.
  표시 처리를 `onOtaChanged`로 옮겼다.
- 순간적으로 지나가는 단계가 400/500ms 화면 수집 사이에 사라졌다.
  `Device._progress`가 업데이트별 단계 이력을 `progress.json`에 함께 기록하고
  feeder가 이를 전달한다. 이력은 표시 전용이며 검증·실행 판단에 사용하지 않는다.
- 새 띠가 이전 애니메이션을 재시작하던 동작을 순차 큐로 교체했다.
- 화면을 처음 열 때 과거 업데이트를 재생하지 않고, 동일 단계는 중복 표시하지 않는다.
- 기존 임시 재생 스크립트는 정상 롤백·공격 차단·정상 업데이트 세 시나리오가
  섞여 있었다. 원본을 백업하고 한 번의 공격 시나리오로 정리했다.

## 검증

- `tests/test_cockpit_transitions.py`: 실제 표시용 JavaScript로 정상 승인,
  정상 복귀, 복구 거부, 복구 실행 실패, 중복 방지, 거부 후 성공 표시 방지를 검사한다.
- 같은 파일에서 실제 `qmlscene`을 offscreen으로 실행해
  `preparing → installed → rollback → rejected` 네 띠가 순서대로 표시됨을 검사한다.
- `tests/test_progress.py`: 빠른 단계 전환이 최종 상태 파일에도 순서대로 남는지 검사한다.
- 임시 재생 스크립트는 출력 경로를 임시 폴더로 바꾸고 대기를 제거해 검증했다.
  결과는 `metadata → verify → recover → recovery_failed`이며 실제 보드 시험이 아니다.
- 전체 pytest 결과: `.demo/cockpit-order-2026-11-12.xml`.

## 보드 적용 시

핫스팟 연결 후 실제 SSH 대상·실행 경로를 재확인한다. `device.py`,
`bench/cockpit_feeder.py`, `Cockpit.qml`, 새 `Transitions.js`를 함께 반영해야 한다.
UI 글꼴도 함께 배포한다. 기존에 설치된 서명 검증 대상 슬롯 내부의 QML을 직접
덮어쓰면 복구 해시가 달라지므로, IVI 디자인은 새 payload를 빌드·서명해 적용한다.
실제 CPU/CAN 이상 감지와 복구 거부 표시는 보드 연결 후 별도로 확인한다.

## 보드 배포 기록

- iPhone 네트워크에서 Pi1 `.2`, Pi2 `.3`, Pi3 `.4` 모두 SSH 성공 및 hostname 확인.
- Pi2 스테이징: `/home/otauser/capstone-cockpit-ui-20261112`.
- Pi2 백업: `/var/backups/capstone-cockpit-1794458438500496441`.
- `/opt/capstone-ivi/display`에 QML/JavaScript/글꼴을 반영했다. feeder와
  `/opt/capstone-ivi/code` 및 벤치 venv의 `device.py`도 백업 후 반영했다.
- 실제 V2 슬롯의 해시 검증 통과. 슬롯 내부 앱 파일은 수정하지 않았다.
- `capstone-cluster`, `capstone-ivi`, `capstone-cockpit-feeder`,
  `capstone-cockpit-ui` 네 서비스의 active 상태 확인.
- Pi3는 사용자 transient unit `capstone-ivi-monitor`를 시작했다.
  출력은 `/home/otauser/ivi-monitor/live-20261112`. CAN 프레임 0개로 blind 상태였다.
- feeder는 Pi2 root SSH의 기존 pinned identity를 사용하며, 이번 실행에는
  `ProxyJump=none`, `HostName=172.20.10.4`를 명시했다. 기존 SSH 설정은 덮어쓰지 않았다.
- 단위들은 transient로 시작했으므로 재부팅 시 자동 실행을 보장하지 않는다.
- 마지막 확인 중 노트북 네트워크가 KMU_WiFi로 전환됐다. iPhone SSID가 스캔에서도
  보이지 않아 Pi1 송신 재개, Pi2 화면 캡처 및 실제 OTA 순서 검증은 완료하지 못했다.
  배포 성공을 전체 시연 성공으로 해석하지 않는다. 핫스팟 복구 후 이 단계부터 재개한다.

## 핫스팟 복구 후 추가 확인 및 화면 수집 간섭 수정

- 세 보드 재접속 성공. Pi1의 `capstone-demo-source`를 실행해 100ms 주기의
  정상 65km/h CAN 입력을 재개했다. Pi2 모델과 실제 계기판 화면에 65가 반영되고
  Pi3에도 입력/응답 프레임이 수집되는 것을 확인했다.
- Pi2 ARM 보드에서 실제 QML을 offscreen으로 실행한 분리 시험은
  `preparing → installed → rollback → rejected` 순서로 통과했다.
  이는 애니메이션 시험이며 실제 OTA/공격 전체 실행 증거는 아니다.
- 복귀 후에도 헤더에 `2 → 3`이 남던 표시를 수정했다. 복귀 완료는 V2만,
  복구 검사 중에는 V3 → V2, 복구 실패는 실패 상태를 표시한다.
- 정상 V2에서 지연 의심이 나타났다. 기존 CPU 분리를 적용해도 p99 약 116ms가
  관찰됐고, 수집기를 멈추자 한 정상 창의 p99가 4.23ms로 내려갔다.
  원인은 Qt 메인 스레드에서 실행되는 `grabWindow()` 캡처 요청의 간섭이었다.
- `bench/cockpit_feeder.py`는 가벼운 모델 snapshot만 요청하고, Pillow/XCB로
  Xvfb `:93`의 기존 픽셀을 외부에서 읽도록 바꿨다. 수집기 실행 중 빠른 표시 지연은
  약 1.5ms로 내려갔다. 100ms 감지 임계값은 변경하지 않았다.
- 화면 수집에는 `pip install '.[cockpit]'` 또는 OS의 Pillow/XCB가 필요하다.
  Pi2에는 이미 설치되어 있었고 호스트 테스트 venv에도 설치했다.
  Pi2 feeder는 `XAUTHORITY=/var/lib/capstone-app/Xauthority`를 사용한다.
  `--cluster-display` 기본값은 `:93`이고 예전 `--cluster-capture`는 호환 인자로만 받는다.
- 실제 Xvfb 화면 캡처와 Unix socket snapshot을 사용하는 회귀 시험을 추가했다.
  `/run`의 CPUAffinity drop-in은 cluster=0, 화면/수집=1~3을 유지한다.
- 증적: `.demo/cockpit-board-20261112/` 아래 화면 PNG, 수집 JSON,
  ARM 애니메이션 로그, 정상 상태 관측 JSONL. 실제 업데이트/복구 슬롯 공격은
  이 UI 적용 작업에서 새로 실행하지 않았다.
- 변경 후 약 45초(감시 누적 44.228초), 9회 관측에서 모두 observing/normal이었다.
  수집된 7개 통계 창 p99는 2.19~4.89ms, 누락률은 모두 0이었다.
  이는 이번 한 번의 정상 상태 관측이며 장시간 안정성 보장은 아니다.
- 최종 로컬 pytest: 271 passed, 18 skipped. 건너뛴 항목은 명시적으로 선택하지 않은
  하드웨어 시험 10개와 vcan0이 없는 환경의 CAN 인증 판정 시험 8개다.
