# Pi에서 선택적으로 실행하기

기존 SD·Qt 계기판·OTA 서비스에는 손대지 않는 **별도 앱 설치 경로**다.
먼저 README의 로컬 시험을 마친다. 이 절차는 자동 실행하지 않는다.

## 파일 전달

노트북에서 HTTP 서버를 Pi가 접근할 수 있도록 연다.

```bash
python3 -m http.server 8080 --bind 0.0.0.0 --directory .demo/releases
```

같은 실험 LAN에서만 사용한다. 공개키는 SSH/USB 등 별도 신뢰 경로로 전달하고,
개인키는 HTTP 디렉터리와 Pi에 복사하지 않는다.

Pi에서는 이 브랜치를 별도 디렉터리에 내려받아 설치한다.

```bash
git clone --branch refactor/minimal-ota https://github.com/jsy202/CAPSTONE_OTA.git CAPSTONE_OTA_MINIMAL
cd CAPSTONE_OTA_MINIMAL
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/ota --root ./device update http://LAPTOP_IP:8080/v1 --public-key /path/to/update.pub
.venv/bin/ota --root ./device status
./device/current/bin/app
```

이 상태는 로컬 모드다. 업데이트의 `--check`가 앱 초기화를 확인하며, 이미 수동 실행한 앱을
자동으로 정지·교체하지 않는다. Ctrl+C로 종료한 뒤 다음 업데이트를 진행한다.

## 상시 서비스가 필요할 때만

새 빈 장치 경로를 사용한다. 기존 local-mode 장치와 혼용하지 않는다.
아래는 관리자 권한으로 설치하는 선택 절차다. 기존 `digital-dash.service`를 교체하지 않는다.

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin capstone-app
sudo install -d -m 0755 /opt/capstone-ota-minimal
sudo python3 -m venv /opt/capstone-ota-minimal/venv
sudo /opt/capstone-ota-minimal/venv/bin/pip install .
sudo install -m 0644 deploy/capstone-central.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo /opt/capstone-ota-minimal/venv/bin/ota --root /opt/capstone-ota-minimal/device \
  update http://LAPTOP_IP:8080/v1 --public-key /path/to/update.pub --systemd
```

이미 사용자가 있다면 `useradd`는 생략한다. 앱은 `capstone-app`으로 실행하고, 설치·공개키·저널은
관리자 소유로 유지한다. 앱에 슬롯 쓰기 권한을 주지 않는다.

단, 설치 중 `bin/app --check`는 업데이터의 권한을 그대로 사용하므로 위 `sudo` 절차에서는
관리자로 실행되며, 상시 서비스의 샌드박스가 적용되지 않는다. 서명 권한은 이 초기화 코드를
실행할 권한도 포함한다. 이 예제는 악의적인 서명자나 서명키 탈취를 방어하지 않는다.

이후 업데이트는 같은 관리자 명령으로 실행한다. `--systemd`와 공개키 선택은 최초 설치에만 필요하다.
서명 검증 → 초기화 검사 → 서비스 재시작·활성 상태 유지까지 통과해야 확정한다.

**이 단계에서는 서비스를 부팅 시 자동 활성화하지 않는다.** 업데이트 중 Pi가 재부팅됐다면
앱을 시작하기 전에 아래 명령으로 복구한다. 자동 부팅 복구 서비스는 이번 단순화 범위에 없다.

```bash
sudo /opt/capstone-ota-minimal/venv/bin/ota --root /opt/capstone-ota-minimal/device update
sudo systemctl start capstone-central.service
```

복구 명령이 실패했다면 서비스를 시작하지 않는다. `rolled_back`은 정상 복구를 뜻하지만,
실패한 업데이트임을 알리기 위해 종료 코드는 2다. `recovery_failed`는 복구 성공이 아니다.
현재 버전을 읽는 `status`는 복구 작업을 수행하지 않는다.

실제 Pi·systemd·전원 차단 시험은 별도 하드웨어 검증이다. 로컬 테스트 통과만으로 완료 처리하지 않는다.
