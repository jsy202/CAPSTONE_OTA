# Cluster 상태 배지 하드웨어 인수 Runbook

이 문서만 순서대로 따라 하면 Raspberry Pi에서 VR-HW-001~003을 기록할 수 있습니다.
- 판정과 증거 기록 형식: [docs/verification/hardware/README.md](verification/hardware/README.md)
- 체크리스트 항목(B1–B8): [RPI_VALIDATION_CHECKLIST.md](RPI_VALIDATION_CHECKLIST.md)

> **먼저 읽기: 지금 할 수 있는 범위**
>
> 전체 OTA 시나리오(Part 2)는 coordinator 검증에 두 가지가 필요합니다.
> - Central Control 앱의 heartbeat(`0x100`)·차량 신호(`0x200`)
> - Qt Cluster 앱의 `functional` IPC 응답
>
> 두 앱 쪽 구현은 아직 없습니다([ZONAL_OTA_GUIDE.md](ZONAL_OTA_GUIDE.md) §5).
> 이 상태에서 Part 2를 실행하면 정상 commit은 일어나지 않고, rollback도 의도한
> `FUNCTIONAL_VALUE_MISMATCH`가 아닌 heartbeat/IPC 부재로 일어납니다. 따라서:
> - **Part 1 (지금 가능):** Cluster Pi 한 대에서 실제 systemd·setpriv·watch 서비스와 실제 화면으로 배지 전환을 확인합니다(B1–B3, B6–B8).
> - **Part 2 (§5 구현 후):** 두 Pi 전체 OTA 시나리오 A/B/C를 실행합니다(B4–B5, VR-HW-001 완결).

## 0. 공통 준비 (Cluster Pi)

`ZONAL_OTA_GUIDE.md` §2–§4가 끝난 상태여야 합니다. 이 저장소는 `~/CAPSTONE_OTA`에,
venv는 `/opt/capstone-ota/venv`에 있다고 가정합니다.

```bash
cd ~/CAPSTONE_OTA && git checkout feature/cluster-status-badge && git rev-parse --short HEAD
uname -m; cat /etc/os-release | head -2; qmake -query QT_VERSION
sudo /opt/capstone-ota/venv/bin/pip install --no-deps --force-reinstall .   # 헬퍼 포함 패키지 갱신
```

**B1 — 패치된 앱 빌드와 Qt 테스트 (VR-HW-003)**

```bash
cd ~/CAPSTONE_OTA/dashboard/volvo-digital-dash
./import-upstream.sh
git clone https://gitlab.com/libeigen/eigen.git upstream/QtDash/eigen \
  && git -C upstream/QtDash/eigen checkout ea2c02060cc329cc83f6a77e8247b195b5defcd9
./customization/apply-customization.sh upstream/QtDash/VolvoDigitalDashModels
./customization/qt-tests/run-qt-tests.sh "$(qmake -query QT_INSTALL_PREFIX)" 2>&1 | tee ~/hw-B1-qt-tests.log
mkdir -p ~/build-dash && cd ~/build-dash && qmake ~/CAPSTONE_OTA/dashboard/volvo-digital-dash/upstream/QtDash/VolvoDigitalDashModels/app/app.pro \
  && make -j4 2>&1 | tee ~/hw-B1-build.log
```

기대 결과: `Totals: 38 passed, 0 failed` (Qt), `Totals: 14 passed, 0 failed` (QML), 빌드 성공.

**공장 버전(1.0.0)과 drill용 1.1.1 payload 준비**

```bash
cd ~/CAPSTONE_OTA/dashboard/volvo-digital-dash
./make-payload.sh ~/build-dash/VolvoDigitalDashModels /tmp/cluster-1.0.0
sudo systemctl stop digital-cluster capstone-ota-zone-agent
sudo install -d -o capstone-ota -g capstone-ota /opt/digital-cluster/slots/A
sudo cp -a /tmp/cluster-1.0.0/. /opt/digital-cluster/slots/A/    # 처음 설치할 때만
sudo install -d -m 0750 -o capstone-ota -g capstone-ota /var/lib/capstone-ota/drill
sudo cp -a /tmp/cluster-1.0.0 /var/lib/capstone-ota/drill/cluster-1.1.1   # 같은 바이너리, 버전 라벨만 1.1.1
sudo chown -R capstone-ota:capstone-ota /var/lib/capstone-ota/drill
```

**B2 — 서비스와 상태 파일**

```bash
sudo install -m 0644 ~/CAPSTONE_OTA/ota/tmpfiles/capstone-ota-ui.conf /etc/tmpfiles.d/
sudo systemd-tmpfiles --create /etc/tmpfiles.d/capstone-ota-ui.conf
sudo install -m 0644 ~/CAPSTONE_OTA/ota/systemd/{digital-cluster,capstone-ota-ui-status}.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now capstone-ota-ui-status digital-cluster capstone-ota-zone-agent
systemctl --no-pager status digital-cluster capstone-ota-ui-status | tee ~/hw-B2-status.txt
cat /run/capstone-ota-ui/digital-cluster.json; stat -c '%U %a %n' /run/capstone-ota-ui/digital-cluster.json
```

기대 결과: 두 서비스 `active`, `{"state":"stable","version":"1.0.0",...}`, 소유자 `capstone-ota`, 모드 `644`.

## Part 1 — 배지 drill (Cluster Pi 단독, 지금 가능)

Drill은 OTA 업데이트가 아닙니다. 서명·다운로드·차량 검증 없이 실제 A/B slot API만
한 단계씩 실행합니다. 매 단계마다 **화면을 촬영**하고 아래 증거 명령을 실행합니다.

```bash
cd ~/CAPSTONE_OTA
sudo install -m 0755 -o capstone-ota -g capstone-ota ota/scripts/cluster_badge_drill.py /var/lib/capstone-ota/drill/
DRILL="sudo -u capstone-ota /opt/capstone-ota/venv/bin/python /var/lib/capstone-ota/drill/cluster_badge_drill.py"
EVID() { mkdir -p ~/hw-evidence/$1; cat /run/capstone-ota-ui/digital-cluster.json > ~/hw-evidence/$1/ui.json;
         sudo cat /opt/digital-cluster/state.json > ~/hw-evidence/$1/slot.json; readlink /opt/digital-cluster/active-slot > ~/hw-evidence/$1/active-slot.txt;
         journalctl -u digital-cluster -u capstone-ota-ui-status --since -2min -o short-iso > ~/hw-evidence/$1/journal.log; }
sudo systemctl stop capstone-ota-zone-agent        # install root는 한 프로세스만 소유
```

**B3 — 공장 상태**

```bash
EVID B3-stable
```

화면: 우하단 `STABLE` / `SW 1.0.0`(녹색), 경고등 줄 가림 없음.

**Drill 1 — trial 후 rollback**

```bash
$DRILL stage --payload /var/lib/capstone-ota/drill/cluster-1.1.1 --version 1.1.1
$DRILL activate && EVID D1-trial      # 화면: OTA TRIAL / SW 1.1.1, cyan 테두리와 상하 선
$DRILL rollback && EVID D1-restored   # 화면: RESTORED / SW 1.0.0 (amber, 최대 15 s)
sleep 20 && EVID D1-stable            # 화면: STABLE / SW 1.0.0
```

**Drill 2 — trial 후 commit (앱 재시작 없이 반영)**

```bash
$DRILL stage --payload /var/lib/capstone-ota/drill/cluster-1.1.1 --version 1.1.1
$DRILL activate && EVID D2-trial
$DRILL commit && sleep 3 && EVID D2-stable   # 화면: 1~2 s 안에 STABLE / SW 1.1.1
systemctl show -p NRestarts -p ActiveEnterTimestamp digital-cluster   # commit 시 재시작 없음 확인
```

**Drill 3 — 상태 파일 손상 (fail-safe)**

```bash
sudo systemctl stop capstone-ota-ui-status
echo '{corrupt' | sudo -u capstone-ota tee /run/capstone-ota-ui/digital-cluster.json
sleep 3 && EVID D3-corrupt       # 화면: SW STATUS / —, 계기판은 정상 동작
sudo systemctl start capstone-ota-ui-status
```

**B6–B8 — 화면 확인**
- **B6:** 같은 신호로 패치 전후 바이너리의 속도·RPM·기어·경고등이 같은지 비교합니다. `slots/A`에 원본 빌드를 잠시 넣고 촬영합니다.
- **B7:** 180° 회전된 실제 화면의 우하단에 배지가 있고 글자가 정방향인지 확인합니다.
- **B8:** 1~2 m 거리에서 촬영한 영상으로 상태 단어와 `SW x.y.z`를 읽을 수 있는지 확인합니다.

**정리**

```bash
sudo systemctl start capstone-ota-zone-agent
```

## Part 2 — 두 Pi 전체 OTA 시나리오 (ZONAL §5 앱 계약 구현 후)

**공통:** 노트북에서 `ZONAL_OTA_GUIDE.md` §6으로 1.1.0(Central)과 1.1.1(Cluster)을 서명하고
`vehicle-package`로 번들 2.0.0을 만듭니다. 상태 구독은 다음 명령으로 켭니다.

```bash
capstone-ota-publish watch --broker 192.168.0.10 --ca ./pki/ca.crt --cert ./pki/publisher.crt \
  --key ./pki/publisher.key --device-id central-pi-01 | tee ~/hw-evidence/mqtt-<scenario>.log
```

각 Pi에서 수집할 증거:

```bash
candump -ta can0 > can0-<scenario>.log &                                   # 0x100/0x101 heartbeat, 0x600/0x601
journalctl -u capstone-ota-coordinator -u central-control -o short-iso > central-<scenario>.log   # Central
journalctl -u capstone-ota-zone-agent -u digital-cluster -u capstone-ota-ui-status -o short-iso > cluster-<scenario>.log
sudo cat /var/lib/capstone-ota/vehicle.json > vehicle-<scenario>.json       # Central: 전이·증거·오류 코드
sudo cat /opt/digital-cluster/state.json; readlink /opt/digital-cluster/active-slot
cat /run/capstone-ota-ui/digital-cluster.json
```

**Scenario A — 정상 commit (B5)**

```bash
capstone-ota-publish vehicle-publish --broker 192.168.0.10 --ca ./pki/ca.crt --cert ./pki/publisher.crt \
  --key ./pki/publisher.key --device-id central-pi-01 \
  --manifest ./releases/2.0.0.vehicle-manifest.json --signature ./releases/2.0.0.vehicle-manifest.sig \
  --base-url https://192.168.0.10:8443/releases
```

기대 결과:
- 화면: `A / STABLE / 1.0.0` → `B / OTA TRIAL / 1.1.1` → `B / STABLE / 1.1.1`
- MQTT phase: `PREPARING → READY → ACTIVATING → VERIFYING → COMMITTED`

**Scenario B — 런타임 결함 강제 (B4)**

Cluster 1.1.1 빌드가 `functional` 응답에서 속도를 1/10로 보고하도록 만든 릴리스를
사용합니다. 무하드웨어 테스트의 `speed_divisor=10`과 같은 결함입니다.

기대 결과:
- 화면: `B / OTA TRIAL / 1.1.1` → `A / RESTORED / 1.0.0` → `A / STABLE / 1.0.0`
- MQTT phase: `… VERIFYING → ROLLING_BACK → RECOVERY_VERIFYING → ROLLED_BACK`
- `vehicle.json`의 `last_error.code` = `FUNCTIONAL_VALUE_MISMATCH`, 검증 scope [trial, recovery] = [false, true]

**Scenario C — 검증 중 전원 차단**

`VERIFYING` 상태가 MQTT에 보이면 두 Pi의 전원을 동시에 차단한 뒤 다시 켭니다.

기대 결과:
- 부팅 후 `ROLLED_BACK` (`UPDATE_INTERRUPTED`), recovery 검증 통과, 두 slot A
- 화면: `STABLE / SW 1.0.0`. `/run`이 비워지므로 RESTORED는 표시되지 않습니다(알려진 제한).

결과는 [hardware/README.md](verification/hardware/README.md) 형식으로 기록하고, 체크리스트 B1–B8에 PASS/FAIL을 적습니다.
