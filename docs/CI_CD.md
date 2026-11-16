# CI/CD

두 워크플로가 있다. 실보드(Pi 3대) 검증은 공용 러너에서 돌릴 수 없어 **자동화에 포함하지 않았다**.

| 워크플로 | 언제 | 하는 일 |
| --- | --- | --- |
| `tests.yml` | push, PR, 수동 | `lint` · `pytest`(Python 3.10/3.12) · `qt` |
| `release.yml` | 태그 `ivi-v*` push, 수동 | 전체 테스트 → IVI 릴리스 생성·서명 → GitHub Release 첨부 |

## CI (`tests.yml`)

- **lint**: `ruff`로 문법 오류·정의되지 않은 이름만 막는다(차단). 미사용 import는 참고용(비차단).
  스타일 규칙은 일부러 껐다. 또 `docs/TEST_CASES.md`가 적은 테스트 함수가 실제로 있는지 검사한다.
- **pytest**: 전체 스위트와 커버리지·JUnit·Allure를 아티팩트로 저장한다. 실행 요약에
  skip 사유별 개수와 TC 추적표를 붙인다. 이 잡은 Qt를 빌드하지 않으므로 Qt 테스트는 skip된다(요약에 그대로 보인다).
- **qt**: 고정 커밋의 업스트림 대시보드를 받아 클러스터·진행 패널을 빌드하고 Xvfb에서 Qt 테스트를 돌린다.
  **하나라도 skip되면 실패**한다(`tools/ci/skip_guard.py`). 초록불이 "Qt를 건너뛰었다"를 뜻하지 않게 하려는 장치다.
  apt 패키지 목록은 CI 실행으로 확인되기 전이라 `continue-on-error: true`다. 첫 성공 후 제거한다.

### 알려진 skip

- 실보드 테스트 10개: `--hardware-config` 없이는 의도적으로 skip.
- `vcan0` 필요 테스트 8개: 공용 러너에서 가상 CAN 모듈을 올리는 설정을 아직 검증하지 못해 skip 상태다.

## 릴리스 (`release.yml`)

```
git tag ivi-v3.0.0 && git push origin ivi-v3.0.0
```

1. 전체 테스트 통과 확인.
2. 태그가 `main`에 포함된 커밋인지 확인.
3. `release/versions.json`에서 버전 → 라벨·SVN을 찾아 IVI 페이로드 생성, Ed25519 서명.
4. **같은 공개키로 서명을 다시 검증**하고 `app.tar.gz` 해시를 `release.json`과 대조. 실패하면 게시하지 않는다.
5. `release.json`, `release.sig`, `app.tar.gz`, `release-public.pem`, `build-info.json`(커밋 ID·해시),
   `SHA256SUMS`를 GitHub Release에 첨부.

수동 실행(workflow_dispatch)은 임시 키로 만들어 아티팩트만 남기고 **Release는 만들지 않는다**.
시크릿이 없는 태그 push는 실패한다.

### 서명 키 등록 (직접 해야 하는 일)

```bash
python - <<'PY'
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
k = Ed25519PrivateKey.generate()
open('ota-release.pem', 'wb').write(k.private_bytes(serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
PY
gh secret set OTA_SIGNING_KEY --env release-signing < ota-release.pem
shred -u ota-release.pem   # 개인키는 저장소에 두지 않는다
```

Settings → Environments → `release-signing`에서 필수 승인자를 지정하고 배포 태그를 `ivi-v*`로 제한하는 것을 권한다.
보드가 신뢰하는 공개키는 이 키의 공개키여야 한다. 공개키는 릴리스마다 `release-public.pem`으로 첨부된다.

### 한계

이 파이프라인은 **서명된 릴리스를 만드는 데까지**다. 보드에 설치하고 롤백하는 일은 기존 OTA 흐름이 맡는다.

## 테스트 추적 (`tools/ci/trace_matrix.py`)

`docs/TEST_CASES.md`의 TC ID를 자동화 함수와 JUnit 결과에 연결한다.

```bash
python -m tools.ci.trace_matrix --junit artifacts/junit.xml --markdown trace-matrix.md --check
```

결과 값: `pass` / `fail` / `skip`(수집됐으나 실행 안 됨, 실보드 전용 포함) / `not-run` / `no-function`(문서에 함수가 없음) / `MISSING`(문서가 적은 함수가 없음 → `--check` 실패).
소프트웨어 테스트에는 TC ID가 없고, TC 문서는 실보드 테스트만 다룬다.
