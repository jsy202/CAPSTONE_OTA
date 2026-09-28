# Raspberry Pi 4B OTA 하드웨어 검증 체크리스트

이 문서는 실제 Pi 한 대에서 반복 실행하고 서명하는 수동 인수 기록입니다. 로컬
자동화 테스트 결과로 대체하지 않습니다.

## 시험 기록

| 항목 | 기록 |
|---|---|
| 시험 일시/시간대 | |
| 시험자 | |
| Pi 자산 번호/장치 ID | |
| Pi 모델/RAM | |
| OS 이미지와 커널 | |
| CPU 아키텍처 | |
| 노트북 OS/IP | |
| 시작 dashboard 버전 | |
| 목표 dashboard 버전 | |
| Git 커밋 | |

판정은 `PASS`, `FAIL`, `N/A` 중 하나로 적고 결과/증거에 명령 출력, MQTT 상태,
journal 시간 범위나 캡처 파일명을 남깁니다.

## 사전 조건

| # | 확인 항목과 방법 | 기대 결과 | 판정 | 결과/증거 |
|---|---|---|---|---|
| P1 | `cat /etc/os-release; uname -m; uname -r` | 승인한 Linux, `aarch64`/설계 대상 아키텍처 | PENDING | |
| P2 | `df -h /opt/digital-dash /var/lib/capstone-ota` | 목표 archive/해제 크기의 2배 이상 여유 | PENDING | |
| P3 | `timedatectl show -p NTPSynchronized -p TimeUSec` | NTP 동기화 `yes`, 노트북과 시간 일치 | PENDING | |
| P4 | `ip -br addr; ping -c 3 <노트북-IP>` | 동일 LAN에서 양방향 도달 | PENDING | |
| P5 | `openssl x509 -in /etc/capstone-ota/pki/cluster-pi-01.crt -noout -subject -dates` | CN=device ID, 유효기간 정상 | PENDING | |
| P6 | HTTPS 서버 인증서 SAN 확인 | 접속에 쓰는 노트북 IP/hostname 포함 | PENDING | |
| P7 | `systemctl status capstone-ota-agent digital-dash` | agent active, 기존 dash active | PENDING | |

## 기능·장애 시험

| # | 시험 절차 | 기대 결과 | 판정 | 결과/증거 |
|---|---|---|---|---|
| F1 | broker를 30초 중지 후 재시작 | agent가 재시작 없이 MQTT 재연결 | PENDING | |
| F2 | 정상 상위 버전 배포 | 모든 단계 후 `success`, 화면/버전 변경 | PENDING | |
| F3 | 배포한 동일 `job_id` 재전달 | 재다운로드 없이 기존 terminal 상태 재발행 | PENDING | |
| F4 | 서명 후 archive 1바이트 변경해 배포 | `DOWNLOAD_SIZE_MISMATCH` 또는 `ARTIFACT_HASH_MISMATCH`, current 불변 | PENDING | |
| F5 | 다운로드 중 Pi Wi-Fi를 끊고 복구 | 부분 파일 미활성화, 기존 dash 계속 동작, agent 재연결 | PENDING | |
| F6 | `downloading`/`staging` 중 agent 강제 재시작 | current 불변, 재시작 후 MQTT 연결, 손상 릴리스 미활성화 | PENDING | |
| F7 | 즉시 종료하는 더 높은 버전 배포 | `health_check` 뒤 `rollback`, 이전 화면 복구 | PENDING | |
| F8 | 정상 업데이트 후 `sudo reboot` | current 버전과 agent/dashboard 서비스 유지 | PENDING | |
| F9 | 현재 이하 SemVer 배포 | `ROLLBACK_REJECTED`, 링크와 서비스 불변 | PENDING | |
| F10 | 다른 device ID용 manifest 전달 | `WRONG_DEVICE`/명령 무시, 링크 불변 | PENDING | |

각 시험 전후에 다음을 기록합니다.

```bash
date --iso-8601=seconds
readlink -f /opt/digital-dash/current /opt/digital-dash/previous
systemctl is-active capstone-ota-agent.service digital-dash.service
sudo python3 -m json.tool /var/lib/capstone-ota/state.json
```

## 로그 수집

시험이 끝나면 시간 범위를 실제 값으로 바꿔 보관합니다.

```bash
mkdir -p validation-logs
sudo journalctl -u capstone-ota-agent.service -u digital-dash.service \
  --since 'YYYY-MM-DD HH:MM:SS' --until 'YYYY-MM-DD HH:MM:SS' \
  --no-pager > validation-logs/services.log
systemctl status --no-pager capstone-ota-agent.service digital-dash.service \
  > validation-logs/final-status.txt
cp /var/lib/capstone-ota/state.json validation-logs/state.json
sha256sum validation-logs/* > validation-logs/SHA256SUMS
```

상태 파일에 운영상 민감한 식별자가 있다면 외부 공유 전에 비식별화합니다. 개인키는
어떤 로그 묶음에도 포함하지 않습니다.

## 최종 승인

| 역할 | 이름 | 판정 | 날짜/서명 | 미해결 이슈 |
|---|---|---|---|---|
| 시험 수행 | | PENDING | | |
| 프로젝트 검토 | | PENDING | | |

현재 저장소에서는 위 하드웨어 시험을 실행하지 않았습니다. 실제 Pi와 Qt ARM
바이너리가 준비된 후 모든 필수 항목이 `PASS`가 되어야 하드웨어 검증 완료로
표시할 수 있습니다.

