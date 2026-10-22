# Raspberry Pi 4B 설치

Pi에는 Linux/systemd, Python 3.10+, Qt 런타임과 앱이 요구하는 Qt SerialPort,
Positioning, SerialBus/QML 모듈이 설치되어 있다고 가정합니다. 앱은 Pi에서
컴파일하지 않고 ARM용 산출물을 받습니다.

## 1. 저장소와 실행 환경

```bash
git clone https://github.com/jsy202/CAPSTONE_OTA.git
cd CAPSTONE_OTA
sudo apt install -y python3 python3-venv policykit-1
```

## 2. Pi 전용 신뢰 자료 배치

노트북에서 안전한 경로로 다음 네 파일만 전달합니다.

- `ca.crt`
- `cluster-pi-01.crt`
- `cluster-pi-01.key`
- `update-signing.pub`

`update-signing.key`, `ca.key`, publisher/server 개인키는 Pi로 복사하지 마세요.

```bash
sudo install -d -m 0755 /etc/capstone-ota/pki
sudo install -m 0644 ca.crt cluster-pi-01.crt /etc/capstone-ota/pki/
sudo install -m 0600 cluster-pi-01.key /etc/capstone-ota/pki/
sudo install -m 0644 update-signing.pub /etc/capstone-ota/update-signing.pub
```

## 3. 설정과 서비스 설치

예제 파일을 복사해 `broker_host`와 `device_id`를 실제 값으로 수정합니다. 인증서
파일명과 device ID/CN은 같아야 합니다.

```bash
cp ota/config/pi.example.json /tmp/agent.json
nano /tmp/agent.json
sudo ota/scripts/install-pi.sh /tmp/agent.json
sudo chown root:capstone-ota /etc/capstone-ota/pki/cluster-pi-01.key
sudo chmod 0640 /etc/capstone-ota/pki/cluster-pi-01.key
sudo systemctl enable --now capstone-ota-agent.service
```

설치 스크립트는 `capstone-ota`와 `digital-dash` 계정을 분리하고, OTA 계정만
릴리스 트리를 수정하도록 만듭니다. 자동 enable은 하지 않으므로 위 명령은 설정
검증 뒤 운영자가 명시적으로 실행합니다.

첫 정상 릴리스가 활성화되면 다음을 실행합니다.

```bash
sudo systemctl enable digital-dash.service
systemctl status --no-pager capstone-ota-agent.service digital-dash.service
```

Pi의 `current`/`previous` 링크와 상태는 각각 `/opt/digital-dash`와
`/var/lib/capstone-ota/state.json`에 저장됩니다.

