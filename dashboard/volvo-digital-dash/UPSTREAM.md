# Volvo240-DigitalDash upstream provenance

- 원본: `https://github.com/whitfijs-jw/Volvo240-DigitalDash`
- 고정 커밋: `793452919127065536bcb7a08f98838fa963d75e`
- 라이선스: MIT (`LICENSE.upstream` 참조)
- 가져오는 범위: `QtDash/VolvoDigitalDashModels`의 Qt/C++/QML 애플리케이션과 앱 리소스
- 제외 범위: OS 이미지 빌드 트리, 회로·PCB·CAD 자료, 원본 저장소에 남은 호스트 빌드 산출물

`./import-upstream.sh`는 네트워크에서 위 커밋만 가져와 기본적으로
`upstream/QtDash/VolvoDigitalDashModels`에 배치합니다. 기존 대상이 있으면 덮어쓰지
않습니다. 원본의 Eigen 의존성은 별도 서브모듈이므로 빌드 환경에서 Eigen
`ea2c02060cc329cc83f6a77e8247b195b5defcd9`를
`upstream/QtDash/eigen`에 체크아웃해야 합니다.

이 저장소는 원본 앱 소스를 복사하거나 직접 고쳐 보관하지 않습니다. 로컬 변경은
`customization/`에만 있고, import한 뒤 다시 적용할 수 있습니다.

```bash
./import-upstream.sh                                   # 고정 커밋 가져오기(변경 없음)
./customization/apply-customization.sh upstream/QtDash/VolvoDigitalDashModels
```

- `customization/overlay/`: 새 파일만 둡니다 (`AppStatus` C++ 모델, `StatusBadge.qml`).
- `customization/patches/0001-capstone-ota-status-badge.patch`: upstream 파일 4개
  (`app/src/main.cpp`, `app/main.qml`, `app/qml.qrc`, `app/app.pro`)에 **줄 추가만** 합니다.
  upstream이 CRLF를 쓰므로 `.gitattributes`로 패치 바이트를 보존합니다.
- 적용 스크립트는 import가 아닌 트리나 두 번째 적용을 거부하고, 고정 커밋을
  `.capstone-customization-applied`에 기록합니다.
- `customization/qt-tests/`: Qt Test, QML TestCase, 데스크톱 화면 캡처 도구입니다.

빌드한 ARM 실행 파일은 이전과 같이 `make-payload.sh`로 OTA payload에 감쌉니다.

