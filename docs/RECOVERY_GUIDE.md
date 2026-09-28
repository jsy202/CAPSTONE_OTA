# 장애 복구 가이드

## 1. 먼저 상태 수집

```bash
sudo systemctl status --no-pager capstone-ota-agent.service digital-dash.service
sudo journalctl -u capstone-ota-agent.service -u digital-dash.service -n 200 --no-pager
sudo readlink -f /opt/digital-dash/current
sudo readlink -f /opt/digital-dash/previous
sudo python3 -m json.tool /var/lib/capstone-ota/state.json
```

`failed`의 오류 코드가 서명/해시/만료/버전 문제라면 키나 검사를 우회하지 말고
노트북에서 새 job과 올바른 상위 버전을 다시 패키징하세요.

## 2. 자동 롤백 확인

새 앱이 헬스체크를 통과하지 못하면 agent가 `current`를 기존 릴리스로 되돌리고
dashboard를 다시 시작합니다. `rollback` 상태와 `current` 링크가 일치하면 추가
수동 조작 없이 실패 원인을 수정한 더 높은 버전을 배포합니다.

## 3. agent도 동작하지 않을 때 수동 복구

릴리스 디렉터리가 정상이고 `previous`가 가리키는 대상이 확인된 경우에만 합니다.

```bash
sudo systemctl stop capstone-ota-agent.service digital-dash.service
previous=$(sudo readlink -f /opt/digital-dash/previous)
case "$previous" in /opt/digital-dash/releases/*) ;; *) echo "unsafe previous path"; exit 1;; esac
sudo ln -s "releases/$(basename "$previous")" /opt/digital-dash/.current.recovery
sudo mv -Tf /opt/digital-dash/.current.recovery /opt/digital-dash/current
sudo systemctl start digital-dash.service capstone-ota-agent.service
```

`releases/`나 상태 파일을 바로 삭제하지 마세요. 다운로드 중 `.part`와 staging
항목은 활성 `current`에 영향을 주지 않습니다. 디스크 정리는 서비스 정지와 백업
후 별도 유지보수 창에서 수행합니다.

## 4. 인증서/시간 문제

TLS 오류이면 양쪽 시간, 서버 인증서 SAN과 접속 IP, CA 파일을 순서대로 확인합니다.

```bash
timedatectl status
openssl x509 -in /etc/capstone-ota/pki/ca.crt -noout -subject -dates
openssl x509 -in /etc/capstone-ota/pki/cluster-pi-01.crt -noout -subject -dates
```

