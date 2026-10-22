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

이 저장소의 로컬 변경은 원본 앱 소스를 직접 수정하지 않습니다. ARM용으로 빌드된
실행 파일을 `make-payload.sh`로 OTA payload 구조에 감싸는 방식으로 연동합니다.

