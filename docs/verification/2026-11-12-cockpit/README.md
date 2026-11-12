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
