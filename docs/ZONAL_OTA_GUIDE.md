# Zonal 다중 ECU OTA 가이드

Raspberry Pi 4B 두 대로 구성한 **애플리케이션 A/B 슬롯** 기반 zonal OTA
시연 환경입니다. Central HPC가 서명된 vehicle bundle을 받아 Central Control과
Digital Cluster 애플리케이션을 함께 trial 활성화하고, CAN 관측으로 호환성을
검증한 뒤 둘 다 commit하거나 둘 다 이전 슬롯으로 되돌립니다.

> Uptane의 Primary/Secondary 구조를 참고한 **비양산** 설계입니다. Uptane/TUF를
> 구현하지 않았고, OS·커널·부트로더·coordinator·agent 자체는 업데이트하지
> 않습니다. CAN의 CRC-8과 카운터는 오류/신선도 검사일 뿐 인증이 아닙니다.

## 1. 구성

```text
 Laptop (MQTT broker, HTTPS releases)
   │ Wi-Fi (wlan0): MQTT mTLS 8883, HTTPS 8443
   ▼
 Central HPC  central-pi-01                 Digital Cluster  cluster-pi-02
 ├ capstone-ota-coordinator (고정 경로)       ├ capstone-ota-zone-agent (고정 경로)
 ├ central-control.service  (A/B 슬롯)        ├ digital-cluster.service (A/B 슬롯)
 ├ eth0 10.10.0.1/24 ── 직결 Ethernet ── eth0 10.10.0.2/24
 │   └ 비공개 HTTPS 캐시 :8443 ──────────▶ Cluster 아티팩트 다운로드
 └ can0 500 kbit/s ═══ Classic CAN (120 Ω 양단 종단) ═══ can0
     명령 0x600/0x601, heartbeat 0x100/0x101, 차량 신호 0x200, 기능시험 0x610/0x611
```

- MQTT는 노트북 ↔ Central 사이에서만 씁니다. Cluster는 MQTT 자격 증명이 없습니다.
- 아티팩트는 HTTPS로만 전송하고 CAN으로는 보내지 않습니다.
- Cluster는 `https://10.10.0.1:8443/transactions/<token>/cluster/`의
  `transaction.json`, `manifest.json`, `manifest.sig`, `artifact.tar.gz` 네 파일만 받습니다.
  Cluster도 Central과 같은 업데이트 공개키로 릴리스 서명을 직접 검증합니다.

## 2. 하드웨어와 OS 준비 (두 Pi 공통)

1. MCP2515 CAN HAT을 장착하고 `/boot/firmware/config.txt`에 추가합니다.
   HAT의 크리스털 주파수와 인터럽트 핀을 반드시 실물에 맞추세요.

   ```text
   dtparam=spi=on
   dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25
   ```

2. CAN_H/CAN_L/GND를 연결하고 **버스 양 끝에 120 Ω 종단**을 켭니다.
   전원을 끈 상태에서 CAN_H–CAN_L 저항이 약 60 Ω이면 정상입니다.
3. systemd-networkd 파일을 설치합니다.

   ```bash
   sudo install -m 0644 ota/network/80-can0.network /etc/systemd/network/
   # Central
   sudo install -m 0644 ota/network/10-eth0-central.network /etc/systemd/network/
   # Cluster
   sudo install -m 0644 ota/network/10-eth0-cluster.network /etc/systemd/network/
   sudo systemctl enable --now systemd-networkd systemd-networkd-wait-online
   ```

   Wi-Fi(`wlan0`)는 기존 방식대로 노트북과 같은 LAN에 연결합니다. `eth0`에는
   게이트웨이를 두지 않습니다.
4. 확인: `ip -details link show can0`에 `bitrate 500000`, `ping 10.10.0.2`가 성공해야 합니다.

## 3. PKI와 서명키

노트북에서 기존 개발 PKI를 만든 뒤 zonal 인증서를 추가 발급합니다.

```bash
ota/scripts/generate-dev-pki.sh 192.168.0.10 cluster-pi-02 ./pki
ota/scripts/issue-zonal-certs.sh ./pki
```

| 파일 | 설치 위치 | 용도 |
|---|---|---|
| `ca.crt` | 두 Pi `/etc/capstone-ota/pki/` | MQTT·HTTPS 서버 검증 |
| `central-pi-01.crt/.key` | Central | MQTT 클라이언트 (CN = device_id, ACL 패턴 일치) |
| `central-https.crt/.key` | Central | 비공개 캐시 서버, SAN `IP:10.10.0.1` |
| `update-signing.pub` | 두 Pi `/etc/capstone-ota/` | 릴리스·번들 서명 검증 |

`update-signing.key`와 `ca.key`는 노트북 밖으로 복사하지 않습니다. 기존
`acl.template`의 `pattern` 규칙이 `central-pi-01`에도 그대로 적용됩니다.

## 4. 서비스 설치

두 Pi 모두 `/opt/capstone-ota/venv`에 이 저장소를 설치합니다
(`docs/RASPBERRY_PI_SETUP.md` 1단계와 동일).

**Central HPC**

```bash
sudo useradd --system --shell /usr/sbin/nologin capstone-ota      # 없으면
sudo useradd --system --shell /usr/sbin/nologin central-control
sudo install -d -m 0750 -o capstone-ota -g capstone-ota /var/lib/capstone-ota /var/cache/capstone-ota
sudo install -d -m 0755 -o capstone-ota -g capstone-ota /opt/central-control
sudo install -m 0640 -o root -g capstone-ota ota/config/coordinator.example.json /etc/capstone-ota/coordinator.json
sudo install -m 0644 <공장 기준 번들> /etc/capstone-ota/stable-bundle.json
sudo install -m 0644 ota/systemd/capstone-ota-coordinator.service ota/systemd/central-control.service /etc/systemd/system/
sudo install -m 0644 ota/polkit/50-capstone-ota-zonal.rules /etc/polkit-1/rules.d/
```

**Digital Cluster**

```bash
sudo useradd --system --shell /usr/sbin/nologin capstone-ota      # 없으면
sudo groupadd --system digital-cluster
sudo useradd --system --shell /usr/sbin/nologin -g digital-cluster digital-dash  # 이미 있으면 usermod -aG
sudo install -d -m 0750 -o capstone-ota -g capstone-ota /var/lib/capstone-ota
sudo install -d -m 0755 -o capstone-ota -g capstone-ota /opt/digital-cluster
sudo install -m 0640 -o root -g capstone-ota ota/config/cluster-zonal.example.json /etc/capstone-ota/zonal.json
sudo install -m 0644 ota/systemd/capstone-ota-zone-agent.service ota/systemd/digital-cluster.service /etc/systemd/system/
sudo install -m 0644 ota/polkit/50-capstone-ota-zonal.rules /etc/polkit-1/rules.d/
# 화면 상태 배지(표시 전용): 상태 파일 디렉터리와 commit 감시 서비스
sudo install -m 0644 ota/tmpfiles/capstone-ota-ui.conf /etc/tmpfiles.d/
sudo systemd-tmpfiles --create /etc/tmpfiles.d/capstone-ota-ui.conf
sudo install -m 0644 ota/systemd/capstone-ota-ui-status.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now capstone-ota-ui-status
```

- 설정 파일은 엄격하게 검증됩니다. `device_id`, `ecu_id`, `can0`, `500000`,
  `10.10.0.1:8443`, `central-control.service`는 고정값이며 다르면 시작하지 않습니다.
- `stable-bundle.json`은 처음 설치한 두 앱의 버전 집합입니다.
  `ota/config/stable-bundle.example.json`의 버전/프로토콜을 실제 공장 버전으로 맞추고,
  `capstone-ota-zone-agent.service`의 `--initial-version`도 같은 Cluster 버전으로 맞춥니다.
  `digital-cluster.service`와 `capstone-ota-ui-status.service`의 `--initial-version`도 같은 값이어야
  합니다(`tests/integration/test_ui_status_assets.py`가 확인).
- 슬롯 디렉터리는 agent/coordinator가 처음 실행될 때 `slots/A`와 `active-slot`으로
  만들어집니다. 공장 버전 애플리케이션을 `slots/A`에 넣은 뒤 서비스를 켭니다.
- 시작 순서: `can0`·`eth0` 준비 → 애플리케이션 서비스 → `capstone-ota-zone-agent`
  → `capstone-ota-coordinator`. coordinator는 시작 시 복구를 먼저 끝낸 뒤에만 MQTT를 구독하고,
  저널이 `RECOVERY_FAILED`이면 구독하지 않고 종료합니다.

## 5. 애플리케이션 계약

두 계약 모두 이 저장소에 구현돼 있습니다(`feature/zonal-application-contracts`).
- **Central Control:** `apps/central-control/`(Python slot payload)
  - heartbeat에 넣는 version/state는 `capstone-ota-ui-status`가 만든 `/run/capstone-ota-ui/central-control.json`에서 읽습니다. 값을 하드코딩하지 않으며, 알 수 없으면 heartbeat를 보내지 않습니다.
  - maintenance 중에는 정지 상태 신호 `(0 km/h, 800 rpm, P)`를 계속 보냅니다.
- **Digital Cluster:** Qt overlay의 `ClusterIpcServer`
  - GUI 이벤트 루프 위의 `QLocalServer`가 upstream 모델(`speedoModel`, `rpmModel`, 경고등 모델)에 값을 쓰고, 그 모델에서 다시 읽어 응답합니다.
- **결함 빌드(데모·시험 전용):** `qmake CAPSTONE_FAULT_SPEED_DIVISOR=10`으로 빌드하면 표시와 응답이 함께 1/10로 틀립니다. 결함 payload는 `customization/make-fault-payload.sh`로만 만들 수 있고, 일반 `make-payload.sh`는 이 바이너리를 거부합니다.

| 앱 | CAN 송신 | IPC 소켓 | IPC operation |
|---|---|---|---|
| Central Control | `0x100` heartbeat 1 s, `0x200` 차량 신호 100 ms | `/run/central-control/ota.sock` | `maintenance` `{enabled}` → `{enabled}` |
| Digital Cluster | 없음 (heartbeat은 zone agent가 송신) | `/run/digital-cluster/ota.sock` | `functional` `{speed,rpm,gear,warnings,test_id}` → 화면에 해석된 `{speed,rpm,gear,warnings}`; `vehicle` `{speed,rpm,gear,warnings}` |

IPC는 연결당 한 줄 JSON 요청/응답입니다.

```json
{"schema_version":1,"request_id":"<hex>","operation":"functional","payload":{...}}
{"schema_version":1,"request_id":"<같은 값>","ok":true,"result":{...}}
```

`functional` 응답은 요청값을 그대로 돌려주면 안 되고, 앱이 실제로 해석·표시한 값이어야
합니다. 이 값이 런타임 의미 결함을 잡는 근거입니다. 신호 단위는 속도 0.1 km/h,
RPM, 기어(P=0, R=1, N=2, D=3), 경고 비트마스크이며, 모든 CAN 프레임의 byte 7은
CRC-8/SMBUS입니다(`capstone_ota/common/can_protocol.py`).

### 5.1 화면 상태 배지 (OTA 계약 변경 없음)

Cluster 앱은 OTA IPC나 CAN을 통해 상태를 받지 않습니다.
- **상태 파일 생성:** 읽기 전용 헬퍼 `capstone-ota-ui-status`가 slot journal(`0600`)과
  `active-slot`을 읽어 `/run/capstone-ota-ui/digital-cluster.json`(`0644`)을 씁니다.
- **실행 시점:** `digital-cluster.service`의 `ExecStartPre=-+`로 앱이 시작되기 직전에 한 번 실행합니다.
  commit처럼 앱이 재시작되지 않는 변화는 `capstone-ota-ui-status.service`가 1초 주기로 반영합니다.
- **앱 쪽:** 패치된 앱의 `AppStatus`가 이 파일을 1초마다 읽어 우하단 배지를 그립니다.
- **실패 시:** 헬퍼가 실패해도 앱은 시작됩니다(`-`). 파일이 없거나 손상되면 `SW STATUS —`로 표시합니다.

```json
{"schema_version":1,"state":"trial","version":"1.1.1","restored_at":null}
```

데스크톱에서 OTA 없이 시연할 때는 상태 파일이 없을 때만
`CAPSTONE_APP_STATE=trial CAPSTONE_APP_VERSION=1.1.1`을 사용합니다.

## 6. 패키징과 배포 (노트북)

두 앱의 릴리스를 **각각** 서명합니다. 같은 `--output`을 쓰므로 두 앱 버전이 같으면
`<version>.tar.gz` 이름이 겹칩니다. 이때는 버전을 다르게 하거나 출력 폴더를 분리하세요.

```bash
capstone-ota-publish package --payload ./central-payload --entrypoint bin/central-control \
  --output ./releases --device-id central-pi-01 --version 1.1.0 \
  --base-url https://192.168.0.10:8443/releases --private-key ./pki/update-signing.key
capstone-ota-publish package --payload ./cluster-payload --entrypoint bin/digital-dash \
  --output ./releases --device-id cluster-pi-02 --version 1.1.1 \
  --base-url https://192.168.0.10:8443/releases --private-key ./pki/update-signing.key
```

`vehicle-spec.json`에는 transaction UUID, 두 타깃의 버전·CAN 선언·아티팩트 크기/해시,
의존성, health policy를 적습니다(형식은 `stable-bundle.example.json` 참고).
`vehicle-package`는 릴리스 서명과 타깃 바인딩을 확인한 뒤 번들에 서명합니다.

```bash
capstone-ota-publish vehicle-package --spec ./vehicle-spec.json --output ./releases \
  --private-key ./pki/update-signing.key --public-key ./pki/update-signing.pub \
  --central-manifest ./releases/1.1.0.manifest.json --central-signature ./releases/1.1.0.manifest.sig \
  --cluster-manifest ./releases/1.1.1.manifest.json --cluster-signature ./releases/1.1.1.manifest.sig \
  --base-url https://192.168.0.10:8443/releases
capstone-ota-publish serve --root ./releases --bind 0.0.0.0 --port 8443 --cert ./pki/https.crt --key ./pki/https.key
capstone-ota-publish watch  --broker 192.168.0.10 --ca ./pki/ca.crt --cert ./pki/publisher.crt \
  --key ./pki/publisher.key --device-id central-pi-01
capstone-ota-publish vehicle-publish --broker 192.168.0.10 --ca ./pki/ca.crt --cert ./pki/publisher.crt \
  --key ./pki/publisher.key --device-id central-pi-01 \
  --manifest ./releases/2.0.0.vehicle-manifest.json --signature ./releases/2.0.0.vehicle-manifest.sig \
  --base-url https://192.168.0.10:8443/releases
```

상태 토픽 `capstone/central-pi-01/ota/status`에는
`PREPARING → READY → ACTIVATING → VERIFYING → COMMITTED` 또는
`… → ROLLING_BACK → RECOVERY_VERIFYING → ROLLED_BACK | RECOVERY_FAILED`가 순서대로 옵니다.
서명 실패처럼 시작 전에 거부된 명령은 `phase: "REJECTED"`로 보고되며 저널은 바뀌지 않습니다.

## 7. 시연 시나리오와 증거 수집

| # | 시나리오 | 만드는 방법 | 기대 결과 | 무하드웨어 테스트 |
|---|---|---|---|---|
| 1 | 정상 commit | 정상 두 릴리스 | `COMMITTED`, 두 슬롯 B | `test_signed_bundle_commits_both_applications_together` |
| 2 | 정적 거부 | Cluster `protocol_major` 2로 서명 | `ABORTED` `CAN_DECLARATION_MISMATCH`, 슬롯 무변경 | `test_declared_can_major_mismatch_is_rejected_before_activation` |
| 3 | 런타임 의미 결함 | 프로토콜은 맞지만 속도를 1/10로 표시하는 Cluster | `ROLLED_BACK` `FUNCTIONAL_VALUE_MISMATCH`, 두 슬롯 A | `test_runtime_semantic_defect_rolls_back_whole_bundle` |
| 4 | heartbeat 손실 | trial 중 zone agent 정지 또는 CAN 분리 | `ROLLED_BACK` (`HEARTBEAT_*`) | `test_cluster_heartbeat_loss_after_activation_rolls_back` |
| 5 | 전원 차단 | `VERIFYING` 중 두 Pi 전원 차단 후 재부팅 | 시작 복구로 `ROLLED_BACK`, 이전 동작 재검증 | `test_restart_during_verification_restores_both_stable_slots` |
| 6 | 아카이브 변조 | 서명 후 `.tar.gz` 1바이트 변경 | `ABORTED`, 어떤 슬롯도 변경 없음 | `test_tampered_archive_aborts_before_either_slot_changes` |

시나리오 4의 무하드웨어 테스트는 trial 중 Cluster heartbeat 송신만 막습니다.
실제 하드웨어에서는 CAN 분리 시 명령 응답도 끊기므로 rollback 자체가 실패할 수 있고,
그 경우 올바른 결과는 `RECOVERY_FAILED`입니다. 하드웨어에서는 zone agent를 멈추는
방법(`sudo systemctl stop capstone-ota-zone-agent`, 이후 재시작)으로 재현하세요.

각 시나리오마다 다음을 저장합니다.

```bash
candump -ta can0 > can0-<scenario>.log &                      # CAN 원본 (can-utils)
journalctl -u capstone-ota-coordinator -u central-control -o short-iso > central-<scenario>.log
journalctl -u capstone-ota-zone-agent -u digital-cluster -o short-iso > cluster-<scenario>.log
sudo cat /var/lib/capstone-ota/vehicle.json > vehicle-<scenario>.json  # 전이·증거·오류 코드
sudo cat /opt/central-control/state.json /opt/digital-cluster/state.json  # 슬롯 상태
readlink /opt/central-control/active-slot /opt/digital-cluster/active-slot
cat /run/capstone-ota-ui/digital-cluster.json                    # 화면 배지가 표시한 상태
journalctl -u capstone-ota-ui-status -o short-iso > ui-status-<scenario>.log
```

화면 배지의 기대 순서는 다음과 같습니다.
- 시나리오 1: `STABLE SW 1.0.0` → `OTA TRIAL SW 1.1.1` → `STABLE SW 1.1.1`
- 시나리오 3, 4: `STABLE SW 1.0.0` → `OTA TRIAL SW 1.1.1` → `RESTORED SW 1.0.0`(최대 15 s) → `STABLE SW 1.0.0`
- 시나리오 5: 재부팅 후 `STABLE SW 1.0.0`. `/run`이 비워지므로 RESTORED는 표시되지 않습니다.

`vehicle.json`의 `events`에는 전이마다 타임스탬프, ECU별 슬롯, 오류, 검증 증거가
남습니다. 화면 표시값은 사진/영상으로 함께 남기세요.

## 8. 검증 상태

| 항목 | 상태 |
|---|---|
| 단위 테스트: 번들, CAN 코덱, A/B 슬롯, zone agent, coordinator, 설정, 런타임 | 실행됨 (`python3 -m pytest -q`) |
| 무하드웨어 통합: 실제 TLS 서버 2개, 실제 슬롯·zone agent, 가상 CAN, 위 6개 시나리오 | 실행됨 (`tests/integration/test_zonal_*.py`) |
| 인증서 스크립트, systemd/network 설정 파일 구문 | 실행/정적 검사 |
| Raspberry Pi 두 대, MCP2515, 실제 SocketCAN, systemd/polkit 동작 | **미실행** |
| Central Control 앱, Qt Cluster IPC·CAN 연동 | **미구현** (5절 계약) |
| 화면 상태 배지: 헬퍼, systemd 자산, 실제 slot 생애주기, 전체 OTA 경로 상태 전이 | 실행됨 (`scripts/verify-cluster-ota-demo.sh`, Gate G1–G7) |
| 화면 상태 배지: Qt 단위/QML 컴포넌트 테스트, 패치된 upstream 데스크톱 빌드·화면 캡처 | 실행됨 (Qt 5.15.2 x86_64, Xvfb). Pi/ARM 아님 |
| 화면 상태 배지: Raspberry Pi 실제 화면 | **미실행** (`docs/RPI_VALIDATION_CHECKLIST.md` B1–B8) |

무하드웨어 테스트는 CAN 주기를 50/100 ms로 줄인 계약을 사용합니다. 실제 기본값은
heartbeat 1 s, 차량 신호 100 ms이며, 검증 창은 5초입니다.
