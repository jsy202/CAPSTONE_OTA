# Linux 노트북 설치

예시는 노트북 IP `192.168.0.10`, 장치 ID `cluster-pi-01`, 저장소 루트에서
실행한다고 가정합니다. IP가 바뀌면 인증서 SAN과 모든 설정/URL을 다시 맞추세요.

## 1. 의존성 설치

Debian/Ubuntu 계열 예시입니다.

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv openssl mosquitto mosquitto-clients
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install .
```

이후 명령의 `capstone-ota-publish`는 `.venv/bin/capstone-ota-publish`로 바꿔도
됩니다.

## 2. 개발용 PKI 생성

```bash
ota/scripts/generate-dev-pki.sh 192.168.0.10 cluster-pi-01 ./pki
```

생성물은 CA, HTTPS/MQTT 서버 인증서, publisher 인증서, Pi 인증서, Ed25519
서명키입니다. 스크립트는 기존 개인키 덮어쓰기를 거부합니다. 정말 재생성할 때만
백업 후 네 번째 인수 `--force`를 사용하세요. `pki/`는 `.gitignore` 대상입니다.

## 3. Mosquitto 설정

```bash
sudo install -d -m 0750 -o root -g mosquitto /etc/capstone-ota/pki
sudo install -m 0644 ./pki/ca.crt ./pki/mqtt.crt /etc/capstone-ota/pki/
sudo install -m 0640 -o root -g mosquitto ./pki/mqtt.key /etc/capstone-ota/pki/mqtt.key
sudo ota/scripts/install-laptop.sh ota/config/laptop.example.json
sudo systemctl restart mosquitto
sudo systemctl status --no-pager mosquitto
```

방화벽에서는 Pi가 있는 LAN에만 TCP 8883과 8443을 허용하세요. 라우터 포트
포워딩으로 인터넷에 노출하지 마세요.

## 4. publisher 자격증명 확인

다음 연결이 성공하면 CA, 인증서와 ACL이 맞습니다.

```bash
mosquitto_sub -h 192.168.0.10 -p 8883 \
  --cafile ./pki/ca.crt --cert ./pki/publisher.crt --key ./pki/publisher.key \
  -t 'capstone/cluster-pi-01/ota/status' -d
```

HTTPS 서버와 실제 배포는 [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)를
따르세요.

