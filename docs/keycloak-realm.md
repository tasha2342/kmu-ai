# Keycloak realm 설정 (configs/keycloak-realm.json)

`docker-compose.yml`의 keycloak 서비스가 `start-dev --import-realm`으로 기동하며
`configs/keycloak-realm.json`을 읽습니다.

## ⚠️ 이 JSON에는 주석을 넣을 수 없다

Keycloak은 realm 파일을 `RealmRepresentation`으로 역직렬화하면서 **모르는 필드를 거부**합니다.
설명을 남기려고 `"_comment_xxx": [...]` 같은 키를 넣으면 이렇게 죽습니다.

```
ERROR: Failed to import realms
ERROR: Unrecognized field "_comment_session" (class org.keycloak.representations.idm.RealmRepresentation),
       not marked as ignorable
```

import 실패는 곧 **기동 실패**입니다. realm이 이미 DB에 있어도(`IGNORE_EXISTING`) 파일 파싱은
매번 하므로, 컨테이너가 뜰 때마다 같은 지점에서 죽어 재시작 루프에 빠집니다.
그러면 로그인·토큰 발급이 전부 멈추고 챗봇도 401로 막힙니다.

설명은 JSON이 아니라 이 문서에 씁니다.

## 세션 수명

| 키 | 값 | 뜻 |
| --- | --- | --- |
| `accessTokenLifespan` | 300 (5분) | 액세스 토큰 수명. 짧게 둬서 탈취 시 피해를 줄이고, 프론트가 미리 갱신한다 |
| `ssoSessionIdleTimeout` | 28800 (8시간) | 유휴 한도. 실제 로그인 유지 기간을 정하는 값 |
| `ssoSessionMaxLifespan` | 86400 (24시간) | 활동 여부와 무관한 절대 한도 |

기본값(유휴 30분)이면 탭을 잠깐 두고 자리를 비우기만 해도 SSO 세션이 죽어 refresh 토큰까지
무효가 되고 재로그인을 요구합니다. 그래서 늘려 둔 값입니다.

## 로그인 상태 유지

| 키 | 값 | 뜻 |
| --- | --- | --- |
| `rememberMe` | true | 로그인 화면에 '로그인 상태 유지' 체크박스를 띄운다 |
| `ssoSessionIdleTimeoutRememberMe` | 604800 (7일) | 체크한 사용자의 유휴 한도 |
| `ssoSessionMaxLifespanRememberMe` | 2592000 (30일) | 체크한 사용자의 절대 한도 |

긴 수명은 체크박스를 켠 사용자에게만 적용됩니다.

## 바꾼 값이 반영되지 않을 때

`--import-realm`은 `IGNORE_EXISTING` 전략이라 **realm이 이미 있으면 통째로 건너뜁니다.**
파일만 고치고 재기동해도 기존 realm은 그대로입니다. 반영 방법은 두 가지입니다.

- **비파괴적**: Admin REST API로 해당 필드만 갱신한다. 사용자·세션이 보존된다.
  ```bash
  TOKEN=$(curl -s -X POST http://127.0.0.1:8082/realms/master/protocol/openid-connect/token \
    -d client_id=admin-cli -d grant_type=password -d username=jdone -d password='<admin-pw>' \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
  curl -s -X PUT http://127.0.0.1:8082/admin/realms/kmu-ai \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"realm":"kmu-ai","accessTokenLifespan":300,"ssoSessionIdleTimeout":28800}'
  ```
- **전체 재생성**: `kmu-ai-keycloak-postgres-data` 볼륨을 지우고 재기동한다.
  realm이 파일 그대로 다시 만들어지지만 **그동안 쌓인 사용자와 세션이 전부 사라집니다.**
