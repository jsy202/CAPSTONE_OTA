# CAPSTONE_OTA

Linux 노트북에서 Raspberry Pi 4B의 Qt 기반 Volvo 240 디지털 클러스터 앱을
업데이트하는 MQTT 제어형 OTA 프로토타입입니다. MQTT는 작은 제어/상태 메시지만
전달하고, 릴리스 파일은 HTTPS로 전송합니다. Ed25519 서명, SHA-256, 장치·버전·만료
검사, 안전한 압축 해제, 원자적 심볼릭 링크 전환, systemd 헬스체크와 자동 롤백을
포함합니다.

> 이 프로젝트는 단일 Raspberry Pi 애플리케이션용 캡스톤 구현입니다. Uptane을
> 준수하는 양산 차량 펌웨어 OTA가 아니며 OS·커널·부트로더·ECU 업데이트에는
> 사용하면 안 됩니다.

## 구성

```text
Linux 노트북                              Raspberry Pi 4B
┌─────────────────────────────┐           ┌────────────────────────────┐
│ package → manifest + 서명   │           │ capstone-ota-agent         │
│ Mosquitto :8883 (mTLS/ACL)  │──MQTTS──▶ │ 명령 검증·상태 발행        │
│ HTTPS release server :8443  │──HTTPS──▶ │ staging → current 전환      │
│ publish / watch CLI         │◀─MQTTS─── │ digital-dash.service       │
└─────────────────────────────┘           └────────────────────────────┘
                  동일 Wi-Fi/LAN (방화벽으로 외부 접근 차단)
```

## 저장소 구조

```text
CAPSTONE_OTA/
├── capstone_ota/
│   ├── common/       # manifest, 오류, Ed25519 서명 검증
│   ├── publisher/    # 패키징, HTTPS 서버, MQTT 배포 CLI (vehicle-* 포함)
│   ├── agent/        # Pi 다운로드, 검증, 설치, 롤백 + A/B 슬롯·zone agent
│   └── coordinator/  # Central HPC: vehicle 트랜잭션, CAN 검증, 캐시 서버
├── dashboard/volvo-digital-dash/
│   ├── import-upstream.sh  # 고정 upstream 커밋의 Qt 앱만 가져오기
│   ├── make-payload.sh     # ARM 실행 파일을 OTA payload로 조립
│   ├── UPSTREAM.md
│   └── LICENSE.upstream
├── ota/
│   ├── broker/       # Mosquitto TLS 설정과 장치별 ACL
│   ├── config/       # 노트북/Pi/zonal 예제 JSON(비밀 없음)
│   ├── network/      # zonal eth0 직결 주소와 can0 500 kbit/s
│   ├── polkit/       # OTA 계정의 dashboard 재시작 권한 제한
│   ├── scripts/      # PKI 생성과 설치 스크립트
│   └── systemd/      # OTA agent와 dashboard 서비스
├── tests/            # 단위 및 실제 로컬 HTTPS 통합 테스트
└── docs/             # 설치·배포·복구·보안 설명서
```

## 준비물

- 노트북: Linux, Python 3.10+, OpenSSL, Mosquitto, Git
- Pi 4B: Linux, systemd, Python 3.10+, Qt 런타임, ARM용 DigitalDash 빌드
- 두 장치가 접근 가능한 동일 Wi-Fi/LAN과 노트북의 고정 IP

배포 전 [노트북 설치](docs/LAPTOP_SETUP.md)와
[Raspberry Pi 설치](docs/RASPBERRY_PI_SETUP.md)를 먼저 완료하세요.

## 5분 로컬 검증

실제 Pi나 Mosquitto 없이 패키지 서명, 신뢰 CA 기반 HTTPS 다운로드, 활성화,
변조 거부, 롤백까지 검증합니다. systemd 호출만 테스트 대역을 사용합니다.

```bash
git clone https://github.com/jsy202/CAPSTONE_OTA.git
cd CAPSTONE_OTA
python3 -m pip install --user -r requirements-dev.txt
python3 -m pytest tests/integration/test_end_to_end_update.py -q
```

예상 결과는 `1 passed`입니다. 전체 검증은 `python3 -m pytest -q`입니다.

## 실제 배포 요약

1. 노트북에서 개발용 CA, MQTT/HTTPS/장치 인증서와 업데이트 서명키를 생성합니다.
2. Pi에는 CA, 해당 Pi의 클라이언트 인증서·개인키, 업데이트 **공개키만** 설치합니다.
3. Mosquitto, HTTPS 서버, Pi의 두 systemd 서비스를 구성합니다.
4. ARM 바이너리로 payload를 만들고 서명된 릴리스를 생성합니다.
5. 상태 구독을 켠 뒤 MQTT 명령을 발행합니다.

```bash
dashboard/volvo-digital-dash/make-payload.sh ./build-arm/VolvoDigitalDashModels ./payload

capstone-ota-publish package \
  --payload ./payload --entrypoint bin/digital-dash \
  --output ./releases --device-id cluster-pi-01 --version 1.0.1 \
  --base-url https://192.168.0.10:8443/releases \
  --private-key ./pki/update-signing.key

capstone-ota-publish publish \
  --broker 192.168.0.10 --ca ./pki/ca.crt \
  --cert ./pki/publisher.crt --key ./pki/publisher.key \
  --device-id cluster-pi-01 \
  --manifest ./releases/1.0.1.manifest.json \
  --signature ./releases/1.0.1.manifest.sig \
  --base-url https://192.168.0.10:8443/releases
```

전체 순서와 병렬 터미널 명령은 [배포 가이드](docs/DEPLOYMENT_GUIDE.md)에 있습니다.

## MQTT 계약

| 용도 | 토픽 | 권한 |
|---|---|---|
| 업데이트 명령 | `capstone/<device_id>/ota/command` | publisher 쓰기, 해당 Pi 읽기 |
| 진행 상태 | `capstone/<device_id>/ota/status` | 해당 Pi 쓰기, publisher 읽기 |

QoS 1과 retained 메시지를 사용합니다. 상태 단계는 `received`, `downloading`,
`verifying`, `staging`, `activating`, `health_check`, `success`, `rollback`,
`failed`입니다. 완료된 `job_id`는 상태 파일에 보존되어 같은 명령이 재전달돼도
설치를 반복하지 않습니다.

## 보안 및 롤백

- MQTT는 포트 8883의 상호 TLS와 인증서 CN 기반 ACL을 사용합니다.
- HTTPS 인증서 검증을 끌 수 없으며, manifest Ed25519 서명 후 artifact 크기와
  SHA-256을 확인합니다.
- 현재 이하 SemVer, 만료된 manifest, 다른 장치 대상, 위험한 tar 항목을 거부합니다.
- `/opt/digital-dash/current`를 원자적으로 전환한 뒤 서비스가 활성 상태가 아니면
  `/opt/digital-dash/previous`로 자동 복구합니다.
- 개발용 CA와 단일 서명키 회전은 자동화되지 않았습니다. 실제 차량/인터넷 노출
  환경에는 HSM, TUF/Uptane 역할 분리, 폐기·감사·원격 attestation 등이 필요합니다.

자세한 위협 모델은 [SECURITY.md](docs/SECURITY.md), 장애 시 절차는
[RECOVERY_GUIDE.md](docs/RECOVERY_GUIDE.md)를 참조하세요.

## 검증 상태

단위·통합 테스트에는 실제 TLS HTTPS 전송, 정상 활성화, 변조 거부와 자동 롤백이
포함됩니다. 다만 현재 환경에는 Raspberry Pi 4B와 실제 Qt ARM 바이너리가 없어
하드웨어 시험은 실행하지 않았습니다. 현장 인수 시
[Raspberry Pi 검증 체크리스트](docs/RPI_VALIDATION_CHECKLIST.md)에 OS/아키텍처,
네트워크 단절, 재부팅 지속성과 로그 증거를 기록하세요.

## Zonal 다중 ECU 확장 (application A/B)

두 번째 단계로 Raspberry Pi 두 대(Central HPC + Digital Cluster)를 하나의 서명된
vehicle bundle로 함께 업데이트합니다. Central의 coordinator가 두 앱을 trial 슬롯에서
활성화하고, Classic CAN(500 kbit/s) heartbeat·차량 신호·기능시험 응답으로 실제 호환성을
확인한 뒤 **둘 다 commit하거나 둘 다 이전 슬롯으로 rollback**합니다. 아티팩트는 Wi-Fi와
직결 Ethernet의 HTTPS로만 전달되고, CAN에는 짧은 명령과 상태만 오갑니다.

```bash
python3 -m pytest tests/integration/test_zonal_end_to_end.py tests/integration/test_zonal_recovery.py -q
```

실제 TLS 서버, 실제 A/B 슬롯과 zone agent, 가상 CAN 위에서 정상 commit, 정적 거부,
런타임 의미 결함 rollback, heartbeat 손실, 전원 차단 복구, 아카이브 변조, 서명 위조
명령 거부를 실행합니다. 하드웨어 시험은 아직 하지 않았고, Central Control 앱과 Qt
Cluster의 IPC/CAN 연동은 앱 쪽 구현이 필요합니다. 설치·계약·시연 절차는
[ZONAL_OTA_GUIDE.md](docs/ZONAL_OTA_GUIDE.md)에 있습니다. Uptane을 참고했지만 준수
구현은 아닙니다.

## Volvo240-DigitalDash

기준 앱은 `whitfijs-jw/Volvo240-DigitalDash`의 MIT 라이선스 Qt/C++/QML
프로젝트이며 커밋 `793452919127065536bcb7a08f98838fa963d75e`에 고정했습니다.
출처, 가져오기 범위와 라이선스는
[UPSTREAM.md](dashboard/volvo-digital-dash/UPSTREAM.md)에 기록되어 있습니다.
