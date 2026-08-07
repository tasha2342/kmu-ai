from typing import Any, Optional, Callable

from keycloak import KeycloakOpenID, KeycloakError, KeycloakPutError

from fastapi import Request, status, HTTPException
from fastapi.security import OAuth2AuthorizationCodeBearer

from urllib.parse import urlparse

from app.config import config
from app.models.auth import AuthData, TokenUserInfo
from app.models.api.common import UserModel
from app.utils.redis import redis_cache
from app.utils.logger import get_logger


logger = get_logger("auth", log_dir="logs")


def raise_keycloak_http_exception(exc: KeycloakError):
    """Keycloak 오류를 HTTP 예외로 변환합니다.

    Args:
        exc (KeycloakError): Keycloak 오류
    """

    response_code = getattr(exc, "response_code", None) or status.HTTP_500_INTERNAL_SERVER_ERROR
    if response_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="사용자 인증/인가 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.",
        ) from exc

    raise HTTPException(
        status_code=response_code,
        detail=getattr(exc, "error_message", str(exc)),
    ) from exc


#region 사용자 인증
whitelist_domains = config.auth.whitelist_domains
auth_list: dict[str, AuthData] = {}

if len(config.auth.servers) == 0:
    raise ValueError("사용자 인증 서버 정보가 없습니다.")

for auth in config.auth.servers:
    auth_data = AuthData(
        keycloak_admin_config=auth,
        keycloak_openid=KeycloakOpenID(
            server_url=auth.server_url,
            realm_name=auth.realm_name,
            client_id=auth.client_id,
            client_secret_key=auth.client_secret_key
        ),
        oauth2=OAuth2AuthorizationCodeBearer(
            authorizationUrl=f"{auth.server_url}/realms/{auth.realm_name}/auth",
            tokenUrl=f"{auth.server_url}/realms/{auth.realm_name}/token"
        )
    )
    auth_list[auth.alias] = auth_data

async def get_user_info(request: Request) -> TokenUserInfo:
    """API Key를 통해 사용자 정보를 가져옵니다.
    
    인증 정보가 `jd-`로 시작하지 않는 경우 토큰 사용자 인증 방식으로 처리합니다.

    Args:
        request (Request): 요청한 클라이언트의 FastAPI Request 객체

    Returns:
        TokenUserInfo: 인증 정보가 포함된 사용자 정보
    """
    
    api_key = request.headers.get("Authorization", None)
    if not api_key or not api_key.startswith("Bearer jd-"):
        return await get_user_info_with_token(request)
    
    api_key = api_key.split(" ")[1]

    query = {"q": f"apiKey:{api_key}", "exact": True}

    user_info = await get_user_info_with_query(query)
    
    # 인증 실패
    if user_info is None:
        # 화이트리스트 도메인 확인
        referer = request.headers.get("referer", None)
        domain = urlparse(referer).netloc if referer is not None else None

        if domain is not None and domain in whitelist_domains:
            return TokenUserInfo(sub=f"whitelist:domain+{domain}")

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 정보가 올바르지 않습니다.",
        )

    user_info.token = api_key
    return user_info

async def get_user_info_with_token(request: Request) -> TokenUserInfo:
    """인증 정보를 통해 사용자 정보를 가져옵니다.

    Args:
        request (Request): 요청한 클라이언트의 FastAPI Request 객체

    Raises:
        HTTPException - 401: 인증 실패 시 예외 발생
        HTTPException - 500: Keycloak 오류 시 예외 발생 또는 알 수 없는 오류 시 예외 발생

    Returns:
        TokenUserInfo: 인증 정보가 포함된 사용자 정보
    """

    user_info: Optional[dict[str, Any]] = None
    user_data = None
    auth_server = None

    for alias, auth_data in auth_list.items():
        try:
            token = await auth_data.oauth2(request)
            # validate=True로 서명·만료·발급자를 검증한다. (Keycloak JWKS 공개키로 검증)
            # validate=False로 두면 서명 없는(alg=none)·위조 토큰도 디코드되어,
            # 실제 sub만 알면 realm_access.roles를 admin으로 위조해 권한을 탈취할 수 있다.
            user_info = await auth_data.keycloak_openid.a_decode_token(token, validate=True)
            user_data = await auth_data.keycloak_admin.a_get_user(user_info["sub"] if user_info is not None else "")
            auth_server = alias
            break
        except HTTPException as exc:
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                continue
            raise exc
        except KeycloakError as exc:
            raise_keycloak_http_exception(exc)
        except ValueError as exc:
            if str(exc) == "Token format unrecognized":
                continue
            # 서명 불일치·만료·형식 오류 등 토큰 검증 실패는 인증 실패(401)로 처리한다.
            # (다음 인증 서버로 넘어가고, 없으면 아래에서 401을 반환한다)
            logger.info(f"토큰 검증에 실패했습니다. ({alias}: {exc})")
            continue
        except Exception as exc:
            # jwcrypto의 InvalidJWSSignature 등 검증 계열 예외는 인증 실패로 처리한다.
            # 예외 클래스명에 JW(JWT/JWS/JWK)나 Signature/Token/Expired가 있으면 검증 실패로 간주한다.
            exc_name = type(exc).__name__
            if any(k in exc_name for k in ("JW", "Signature", "Token", "Expired", "Claim", "Audience")):
                logger.info(f"토큰 검증에 실패했습니다. ({alias}: {exc_name}: {exc})")
                continue
            logger.exception("알 수 없는 오류가 발생했습니다.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="알 수 없는 오류가 발생했습니다.",
            )

    # 인증 실패
    if user_info is None or user_data is None:
        # 화이트리스트 도메인 확인
        referer = request.headers.get("referer", None)
        domain = urlparse(referer).netloc if referer is not None else None

        if domain is not None and domain in whitelist_domains:
            return TokenUserInfo(sub=f"whitelist:domain+{domain}")

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 정보가 올바르지 않습니다.",
        )

    user_groups = await auth_data.keycloak_admin.a_get_user_groups(user_info["sub"])
    groups = [
        group["name"] 
        for group in user_groups
    ] if user_groups and isinstance(user_groups, list) else []

    raw_attrs = user_data.get("attributes")
    attributes = raw_attrs if isinstance(raw_attrs, dict) else {}

    return TokenUserInfo(
        token=token,
        auth_server=auth_server,
        sub=user_info.get("sub", ""),
        username=user_info.get("preferred_username"),
        email=user_info.get("email", ""),
        email_verified=user_info.get("email_verified"),
        family_name=user_info.get("family_name", ""),
        given_name=user_info.get("given_name", ""),
        name=user_info.get("name", ""),
        roles=user_info.get("realm_access", {}).get("roles", []),
        groups=groups,
        attributes=attributes
    )

async def get_user_info_with_query(query: dict[str, Any]) -> Optional[TokenUserInfo]:
    """Query를 통해 사용자 정보를 가져옵니다.

    Args:
        query (dict[str, Any]): 사용자 검색 조건

    Returns:
        Optional[TokenUserInfo]: 인증 정보가 포함된 사용자 정보
    """
    
    user_info: Optional[dict] = None
    groups = []
    auth_server = None
    last_auth_error: HTTPException | KeycloakError | None = None

    for alias, auth_data in auth_list.items():
        try:
            user_infos = await auth_data.keycloak_admin.a_get_users(query)
            if not user_infos or len(user_infos) == 0 or not isinstance(user_infos, list):
                user_info = None
                continue

            user_info = user_infos[0]
            if user_info is None:
                continue
            
            user_groups = await auth_data.keycloak_admin.a_get_user_groups(user_info["id"])
            groups = [
                group["name"] 
                for group in user_groups
            ] if user_groups and isinstance(user_groups, list) else []

            group_roles: dict[str, list[str]] = {}
            roles = []
            
            if user_groups and isinstance(user_groups, list):
                for user_group in user_groups:
                    if user_group["name"] not in group_roles:
                        group_realm_roles = await auth_data.keycloak_admin.a_get_group_realm_roles(user_group["id"])
                        group_roles[user_group["name"]] = [
                            role["name"] 
                            for role in group_realm_roles
                        ] if group_realm_roles and isinstance(group_realm_roles, list) else []
                    roles.extend(group_roles[user_group["name"]])
            roles = list(set(roles))
            auth_server = alias
            break
        except HTTPException as e:
            last_auth_error = e
            continue
        except KeycloakError as e:
            last_auth_error = e
            continue
        except Exception:
            pass

    # 인증 실패
    if user_info is None:
        if isinstance(last_auth_error, KeycloakError):
            raise_keycloak_http_exception(last_auth_error)
        if last_auth_error is not None:
            raise last_auth_error
        return None

    family_name = user_info.pop("lastName", "")
    given_name = user_info.pop("firstName", "")
    name = f"{given_name} {family_name}"

    return TokenUserInfo(
        auth_server=auth_server,
        sub=user_info.pop("id"),
        username=user_info.pop("username"),
        email=user_info.pop("email"),
        email_verified=user_info.pop("emailVerified"),
        family_name=family_name,
        given_name=given_name,
        name=name,
        roles=roles,
        groups=groups,
        attributes=user_info.pop("attributes", {})
    )

async def get_user_info_with_username(username: str) -> Optional[TokenUserInfo]:
    """사용자명을 통해 사용자 정보를 가져옵니다.

    Args:
        username (str): 사용자명
    
    Returns:
        Optional[TokenUserInfo]: 인증 정보가 포함된 사용자 정보
    """
    
    query = {"q": f"username:{username}", "exact": True}
    user_info = await get_user_info_with_query(query)
    
    if user_info is None:
        return None
    
    user_info.token = username
    return user_info

async def get_users() -> list[dict]:
    """모든 사용자 정보를 가져옵니다.

    Returns:
        list[dict]: 사용자 정보 목록
    """
    
    users: list[dict] = []
    
    for _, auth_data in auth_list.items():
        users_data = await auth_data.keycloak_admin.a_get_users()
        for user_data in users_data:
            if user_data is None or not isinstance(user_data, dict):
                continue
            
            raw_attrs = user_data.get("attributes")
            attributes = raw_attrs if isinstance(raw_attrs, dict) else {}
            
            users.append({
                "sub": user_data.get("id", ""),
                "username": user_data.get("username", ""),
                "last_name": user_data.get("firstName", ""),
                "first_name": user_data.get("lastName", ""),
                "email": user_data.get("email", ""),
                "attributes": attributes
            })
    return users

def get_user_info_required_roles(roles: list[str]) -> Callable:
    """사용자 정보를 가져오기 위한 권한 확인 데코레이터입니다.
    
    필요한 권한이 없는 경우 `HTTPException - 403` 예외를 발생합니다.

    Args:
        roles (list[str]): 필요한 권한 목록

    Returns:
        Callable: 데코레이터
    """
    
    async def _wrapper(request: Request):
        user_info = await get_user_info(request)
        if user_info and roles:
            check_user_roles(user_info, roles)
        return user_info
    return _wrapper

def check_user_roles(user: TokenUserInfo, roles: list[str]) -> None:
    """사용자 권한 확인 함수입니다.

    Args:
        user (TokenUserInfo): 사용자 정보
        roles (list[str]): 필요한 권한 목록

    Raises:
        HTTPException - 403: 권한 확인 실패 시 예외 발생
    """
    
    user_roles: set = set(user.roles or [])
    if not any(role in user_roles for role in roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="접근 권한이 없습니다.",
        )

async def set_user_attributes(user: TokenUserInfo, key: str, value: Any) -> None:
    """사용자 속성 설정 함수입니다.

    Args:
        user (TokenUserInfo): 사용자 정보
        key (str): 속성 키
        value (Any): 속성 값

    Raises:
        HTTPException - 500: Keycloak 오류 시 예외 발생 또는 알 수 없는 오류 시 예외 발생
    """
    
    if user.auth_server is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="사용자 정보를 찾을 수 없습니다.",
        )
    
    try:
        payload = {
            "id": user.sub,
            "username": user.username,
            "email": user.email,
            "emailVerified": user.email_verified,
            "firstName": user.given_name,
            "lastName": user.family_name,
            "attributes": user.attributes
        }
        payload["attributes"][key] = value
        
        await auth_list[user.auth_server].keycloak_admin.a_update_user(user.sub, payload)
    except KeycloakPutError:
        logger.exception("Keycloak 오류가 발생했습니다.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Keycloak 오류가 발생했습니다.",
        )
    except Exception:
        logger.exception("알 수 없는 오류가 발생했습니다.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="알 수 없는 오류가 발생했습니다.",
        )

# 1분간 동일한 요청 캐싱
@redis_cache(expire=60, return_type=UserModel)
async def get_user_info_with_sub(sub: str) -> Optional[UserModel]:
    """사용자 식별 ID를 통해 사용자 정보를 가져옵니다.

    Args:
        sub (str): 사용자 식별 ID

    Returns:
        UserModel: 사용자 정보
    """
    
    user_info = None
    
    for _, auth in auth_list.items():
        try:
            user_info = await auth.keycloak_admin.a_get_user(sub)
            break
        except:
            pass
    
    if user_info is None:
        return None
    
    return UserModel(
        sub=sub,
        username=user_info["username"],
        last_name=user_info.get("firstName", ""),
        first_name=user_info.get("lastName", ""),
        email=user_info["email"],
    )
#endregion
