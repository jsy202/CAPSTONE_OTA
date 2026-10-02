# 릴리스 배포 가이드

노트북 IP는 `192.168.0.10`, 장치 ID는 `cluster-pi-01` 예시입니다. 배포 전에
Pi와 노트북 시간이 NTP로 동기화됐는지 확인하세요. manifest에는 만료시간이 있어
시간 차이가 크면 거부됩니다.

## 1. Qt 앱 준비

원본 소스가 필요하면 고정 커밋을 가져옵니다.

```bash
dashboard/volvo-digital-dash/import-upstream.sh
```

Qt Creator 또는 Raspberry Pi용 교차 컴파일 환경에서 ARM 실행 파일을 만든 뒤
payload로 조립합니다. 선택적인 세 번째 인수는 설정/런타임 리소스 디렉터리입니다.

```bash
dashboard/volvo-digital-dash/make-payload.sh \
  ./build-arm/VolvoDigitalDashModels ./payload ./runtime-resources
```

## 2. 패키징과 서명

버전은 현재 Pi 버전보다 큰 SemVer여야 합니다.

```bash
capstone-ota-publish package \
  --payload ./payload --entrypoint bin/digital-dash \
  --output ./releases --device-id cluster-pi-01 --version 1.0.1 \
  --base-url https://192.168.0.10:8443/releases \
  --private-key ./pki/update-signing.key --expires-in 3600
```

`1.0.1.tar.gz`, `1.0.1.manifest.json`, `1.0.1.manifest.sig`가 생성됩니다.

## 3. HTTPS와 상태 감시 시작

터미널 A:

```bash
capstone-ota-publish serve --root ./releases --bind 0.0.0.0 --port 8443 \
  --cert ./pki/https.crt --key ./pki/https.key
```

터미널 B:

```bash
capstone-ota-publish watch --broker 192.168.0.10 --port 8883 \
  --ca ./pki/ca.crt --cert ./pki/publisher.crt --key ./pki/publisher.key \
  --device-id cluster-pi-01
```

## 4. 업데이트 명령 발행

터미널 C:

```bash
capstone-ota-publish publish --broker 192.168.0.10 --port 8883 \
  --ca ./pki/ca.crt --cert ./pki/publisher.crt --key ./pki/publisher.key \
  --device-id cluster-pi-01 \
  --manifest ./releases/1.0.1.manifest.json \
  --signature ./releases/1.0.1.manifest.sig \
  --base-url https://192.168.0.10:8443/releases
```

`success`가 표시되고 Pi의 `readlink -f /opt/digital-dash/current`가 새 버전을
가리키는지 확인합니다. `failed`이면 `error.code`와 Pi journal을 확인합니다.

## 롤백 시연

즉시 종료되는 실행 파일을 더 높은 버전으로 패키징해 배포하면
`health_check` 뒤 `rollback`이 발행되고 `current`가 이전 버전으로 복구됩니다.
운영 앱을 고의로 깨뜨리지 말고 별도 시연 Pi에서만 수행하세요.

명령은 retained이지만 완료 `job_id`가 기록되므로 재수신은 무해합니다. 폐기한
명령을 broker에서 제거해야 한다면 publisher 인증서로 해당 command 토픽에 빈
retained 메시지를 발행하세요.

## Zonal 다중 ECU 배포

두 Pi(Central HPC + Digital Cluster)를 함께 업데이트하는 `vehicle-package` /
`vehicle-publish` 흐름, CAN·Ethernet 설정, 시연 시나리오와 증거 수집은
[ZONAL_OTA_GUIDE.md](ZONAL_OTA_GUIDE.md)를 따르세요. 위 단일 Pi 명령은 그대로 동작합니다.
