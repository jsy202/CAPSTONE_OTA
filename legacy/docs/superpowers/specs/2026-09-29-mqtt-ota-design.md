# MQTT 기반 Qt DigitalDash OTA 설계

## 1. 목적

이 프로젝트는 Linux가 설치된 Raspberry Pi 4B에서 실행되는 Qt/C++/QML 기반
Volvo240 DigitalDash 애플리케이션을 동일 Wi-Fi에 연결된 Linux 노트북에서
안전하게 업데이트하는 OTA 프로토타입을 구현한다.

양산차 ECU 전체 펌웨어를 갱신하는 Uptane 완전 구현은 범위에 포함하지 않는다.
대신 단일 Raspberry Pi 애플리케이션 OTA에 전자서명, 무결성 검사,
anti-rollback, 원자적 활성화, 헬스체크 및 자동 복구 원칙을 적용한다.

## 2. 전제와 범위

- Raspberry Pi 4B에는 Linux, systemd, Python 3, OpenSSL 및 Qt 런타임이 설치되어 있다.
- 노트북과 Raspberry Pi는 동일한 신뢰 가능한 Wi-Fi/LAN에 연결된다.
- OTA 대상은 미리 빌드된 ARM용 DigitalDash 실행파일, QML 및 런타임 리소스다.
- Raspberry Pi에서 업데이트 소스를 컴파일하지 않는다.
- Linux 노트북은 MQTT 브로커, HTTPS 파일 서버 및 배포 CLI를 실행한다.
- OS, 부트로더, 커널 및 다른 ECU 펌웨어 업데이트는 제외한다.
- Volvo240-DigitalDash에서 가져온 코드는 원본 MIT 저작권과 라이선스를 유지한다.

## 3. 시스템 구성

```text
Linux laptop
├── Mosquitto broker (MQTT over TLS)
├── HTTPS artifact server
└── ota-publish CLI
    ├── release archive 생성
    ├── manifest 생성
    ├── SHA-256 계산
    ├── Ed25519 서명
    └── update command 발행
              │
              ▼ same Wi-Fi/LAN
Raspberry Pi 4B
├── capstone-ota-agent.service
│   ├── device-specific command 구독
│   ├── 명령과 manifest 검증
│   ├── artifact 다운로드 및 staging
│   ├── release 활성화
│   └── 상태 보고 및 rollback
└── digital-dash.service
    └── /opt/capstone/current 내 애플리케이션 실행
```

MQTT는 제어와 상태 보고에만 사용한다. 실제 업데이트 파일은 HTTPS로
전송한다. 이를 통해 MQTT 메시지 크기와 재조립 문제를 피하고 다운로드 실패를
업데이트 설치 상태와 분리한다.

## 4. 저장소 구조

```text
CAPSTONE_OTA/
├── dashboard/
│   └── volvo-digital-dash/     # Qt/C++/QML 애플리케이션 및 라이선스
├── ota/
│   ├── common/                 # manifest와 공통 데이터 모델
│   ├── publisher/              # 노트북 패키징·서명·발행 CLI
│   ├── agent/                  # Raspberry Pi OTA 에이전트
│   ├── broker/                 # Mosquitto 설정 및 ACL 템플릿
│   ├── systemd/                # agent와 dashboard 서비스 유닛
│   └── scripts/                # 인증서·키 생성 및 설치 도구
├── tests/                      # 단위·통합 테스트
├── docs/                       # 설치, 배포, 복구 및 시연 문서
├── requirements.txt
└── README.md
```

## 5. 릴리스 형식

각 업데이트는 다음 파일로 구성한다.

- `<version>.tar.gz`: ARM 애플리케이션과 리소스
- `<version>.manifest.json`: 서명 대상 메타데이터
- `<version>.manifest.sig`: Ed25519 서명

manifest에는 다음 필드를 포함한다.

```json
{
  "schema_version": 1,
  "job_id": "고유 UUID",
  "device_id": "cluster-pi-01",
  "version": "1.0.1",
  "created_at": "RFC 3339 UTC",
  "expires_at": "RFC 3339 UTC",
  "artifact_url": "https://laptop:8443/releases/1.0.1.tar.gz",
  "artifact_size": 123456,
  "artifact_sha256": "64자리 16진수",
  "entrypoint": "bin/digital-dash"
}
```

manifest 직렬화는 정렬된 키와 공백 없는 UTF-8 JSON으로 고정한다. 개인키는
노트북에만 두며 Raspberry Pi에는 검증용 공개키만 설치한다.

## 6. MQTT 계약

기본 토픽은 다음과 같다.

- 명령: `capstone/<device_id>/ota/command`
- 상태: `capstone/<device_id>/ota/status`

명령 메시지는 manifest URL, 서명 URL, `job_id`, `device_id`, `version`을 담는다.
QoS 1을 사용하며 명령은 retained 메시지로 발행해 잠시 오프라인이던 장치도
복귀 후 업데이트를 확인할 수 있게 한다. 에이전트는 완료한 `job_id`와 현재
버전을 영속 상태에 기록해 중복 메시지를 무해하게 처리한다.

상태는 `received`, `downloading`, `verifying`, `staging`, `activating`,
`health_check`, `success`, `rollback`, `failed` 중 하나이며 단계, 진행률,
버전, 오류 코드와 사람이 읽을 수 있는 메시지를 포함한다.

## 7. 보안 설계

### 통신

- MQTT는 TLS 포트만 노출한다.
- 노트북 배포자와 각 Raspberry Pi는 서로 다른 자격증명을 사용한다.
- Mosquitto ACL은 Pi가 자신의 명령 토픽만 구독하고 자신의 상태 토픽만
  발행하도록 제한한다.
- HTTPS 서버 인증서는 프로젝트 로컬 CA로 검증하며 인증서 검증을 끄는 옵션은
  제공하지 않는다.

### 업데이트 신뢰

- Ed25519 공개키로 manifest 서명을 먼저 검증한다.
- 서명된 manifest의 SHA-256 및 크기와 다운로드 파일을 비교한다.
- `device_id`, 스키마 버전, 생성·만료시간과 SemVer를 검사한다.
- 정상 업데이트에서는 현재 이하 버전을 거부해 rollback/replay 공격을 줄인다.
- 동일 `job_id`의 재전송은 기존 결과를 다시 보고하고 설치를 반복하지 않는다.

### 파일과 권한

- 압축 파일 전체 크기와 압축 해제 후 파일 수·총 크기에 제한을 둔다.
- 절대경로, `..`, 심볼릭 링크 및 특수 파일이 포함된 압축은 거부한다.
- OTA 에이전트만 릴리스 디렉터리를 수정할 수 있다.
- 계기판 서비스는 비권한 사용자로 실행한다.
- 임시 파일과 상태 파일은 같은 파일시스템에서 원자적으로 교체한다.

이 설계는 Uptane 완전 준수를 주장하지 않는다. 단일 장치 캡스톤 범위에서
TUF/Uptane의 서명 메타데이터, 만료, 대상 식별 및 anti-rollback 원칙을 축소
적용한다.

## 8. 설치와 롤백

Raspberry Pi의 배치 구조는 다음과 같다.

```text
/opt/capstone/
├── current -> releases/1.0.1
├── previous -> releases/1.0.0
├── releases/
│   ├── 1.0.0/
│   └── 1.0.1/
└── staging/

/var/lib/capstone-ota/state.json
/etc/capstone-ota/config.json
/etc/capstone-ota/update-public-key.pem
```

에이전트는 staging에 다운로드하고 검증과 안전한 압축 해제를 모두 완료한 뒤
릴리스 디렉터리로 원자적으로 이동한다. `previous`를 기존 `current`로 설정한
후 `current`를 새 버전으로 바꾸고 계기판 서비스를 재시작한다.

새 서비스가 정해진 제한시간 안에 `active` 상태를 유지하지 못하면 `current`를
`previous`로 되돌리고 다시 시작한다. 실패한 버전과 오류는 상태 파일 및 MQTT에
기록한다. 전원 차단으로 staging 파일이 남아도 현재 활성 릴리스에는 영향을
주지 않으며 다음 시작 시 정리한다.

초기 버전의 헬스체크는 systemd 서비스가 15초 동안 활성 상태를 유지하는지
확인한다. 후속 단계에서는 DigitalDash가 준비 파일 또는 systemd notify 신호를
보내는 애플리케이션 수준 헬스체크로 확장할 수 있다.

## 9. 오류 처리

- MQTT 연결 실패: 지수 백오프로 재연결하고 현재 앱은 계속 실행한다.
- HTTPS 중단: 부분 파일을 활성화하지 않고 실패 상태를 보고한다.
- 서명·해시·대상 불일치: 설치 전에 즉시 거부한다.
- 저장공간 부족: 다운로드 전에 예상 크기와 보존 공간을 확인한다.
- 잘못된 압축 파일: 안전 검사 실패 후 staging을 폐기한다.
- 서비스 시작 실패: 즉시 이전 릴리스로 롤백한다.
- 에이전트 재시작: 영속 상태와 디렉터리를 대조하여 중단된 작업을 실패 처리하고
  마지막 정상 릴리스를 보존한다.

## 10. 테스트 전략

### 단위 테스트

- canonical manifest 직렬화와 검증
- 정상·손상·잘못된 키 서명 검증
- 버전 비교, 만료 및 대상 장치 확인
- 중복 `job_id` 처리
- tar 경로 탈출, 심볼릭 링크, 크기 제한 거부
- 상태 파일의 원자적 저장

### 통합 테스트

- 로컬 Mosquitto와 HTTPS 서버를 사용한 배포 성공
- 다운로드 중단 시 현재 릴리스 보존
- 변조된 artifact와 manifest 거부
- 계기판 서비스 실패 시 previous로 롤백
- 에이전트 재시작 후 일관성 회복

### Raspberry Pi 시연

1. 버전 1.0.0 계기판 실행을 확인한다.
2. 노트북에서 1.0.1 배포 명령을 실행한다.
3. MQTT 상태 진행을 관찰하고 화면 변경을 확인한다.
4. 변조 패키지가 거부되는 것을 시연한다.
5. 고의로 실패하는 버전을 배포해 자동 롤백을 확인한다.

## 11. 문서 산출물

- 루트 `README.md`: 프로젝트 개요, 아키텍처, 빠른 시작, 보안 및 시연 흐름
- `docs/LAPTOP_SETUP.md`: Mosquitto, 인증서, HTTPS 서버와 배포 CLI 설정
- `docs/RASPBERRY_PI_SETUP.md`: Pi 에이전트와 systemd 설치
- `docs/DEPLOYMENT_GUIDE.md`: 릴리스 생성, 발행, 상태 확인
- `docs/RECOVERY_GUIDE.md`: 수동 롤백, 로그 확인 및 장애 복구
- `docs/SECURITY.md`: 위협 모델, 키 관리, 제한 사항과 양산 환경 차이

## 12. 완료 기준

- 테스트용 payload를 노트북에서 Pi 역할의 로컬 테스트 환경에 배포할 수 있다.
- 정상 업데이트는 새 버전을 활성화하고 MQTT로 성공을 보고한다.
- 서명 또는 해시가 잘못된 업데이트는 활성 릴리스를 변경하지 않는다.
- 새 애플리케이션 실행 실패 시 이전 버전이 자동 복구된다.
- README와 세부 사용설명서만으로 노트북과 Pi를 설치하고 시연할 수 있다.
