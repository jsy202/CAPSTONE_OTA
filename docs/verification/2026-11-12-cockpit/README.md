# 2026-11-12 콕핏 검증 증적

| 파일 | 내용 |
|---|---|
| `summary.json` | 정상 CAN 관측 요약. 전체 OTA·공격 재시연 미실행 여부 포함 |
| `baseline-external-framebuffer.jsonl` | Pi3의 정상 상태 관측 9건 |
| `order-check.log` | 실제 Pi2 ARM Qt에서 분리 실행한 네 단계 애니메이션 순서 |
| `pytest.xml` | 호스트 전체 pytest의 JUnit 결과. 하드웨어 미선택/vcan0 부재 항목은 skip |

시험 절차와 한계는 [시행착오 기록](../../FAILURE_LOG_2026-11-12.md) 및
[배포 기록](../../COCKPIT_EVENT_ORDER_2026-11-12.md)에 정리했다.
이 로그의 정상 관측은 실제 CAN, 애니메이션 순서 검사는 분리 시험이다.
두 결과를 합쳐 전체 OTA 공격 시연에 성공했다고 주장하지 않는다.

## 보드 없이 노트북에서 화면 시험

```bash
cd /home/jsy/CAPSTONE_OTA/.worktrees/physical-can-ota
.venv/bin/python -m demo.local_cockpit_replay
```

`qmlscene`과 `pip install '.[cockpit]'` 의존성이 필요하다. 창을 열고
준비 → 설치 완료·시험 운전 → 지연 의심/확정 → 롤백 준비 → 복구 거부를 재생한다.
계기판 이미지는 고정이고 IVI·통계·이벤트는 합성 데이터다. 실제 CPU 워커,
CAN 송수신, OTA 설치나 SSH 접속을 실행하지 않는다.

실행 파일과 생성 데이터는 `.demo/local-cockpit/`에 격리한다. `/run`이나
설치된 슬롯을 쓰지 않는다. 이미지와 지도 자료가 로컬에 없으면 계기판은
대체 이미지로, 지도는 자료 없이 표시된다. 창을 닫거나 Ctrl+C로 종료한다.
창 없이 파일 준비만 확인하려면 `--prepare-only`를 붙인다.

## 노트북에서 실제 Pi2 화면 보기

Pi2와 같은 네트워크에 연결하고 기존 SSH 키·호스트 키 설정이 준비된 환경에서 실행한다.
현재 실험실 주소와 SSH 설정 경로는 `demo/remote_cockpit.py`의 `SSH`에 명시되어 있다.

```bash
cd /home/jsy/CAPSTONE_OTA/.worktrees/physical-can-ota
.venv/bin/python demo/remote_cockpit.py
```

다른 터미널에서 다음 명령으로 조회 창을 연다.

```bash
google-chrome --app=http://127.0.0.1:8766 --start-maximized
```

업데이트나 공격 시험을 시작하지 않는다. 종료는 첫 터미널에서 Ctrl+C.
브라우저 화면은 마지막 프레임이 오래됐으면 갱신 중단으로 표시한다.
