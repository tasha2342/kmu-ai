from typing import Optional

from datetime import datetime

from pydantic import BaseModel

from keycloak import KeycloakAdmin, KeycloakOpenID, KeycloakError

from fastapi import HTTPException, status
from fastapi.security import OAuth2AuthorizationCodeBearer

from app.models.config import AuthServerConfig


class AuthData:
    """인증 데이터 클래스
    
    Args:
        keycloak_admin_config (AuthServerConfig): Keycloak 관리자 설정
        keycloak_openid (KeycloakOpenID): Keycloak OpenID 설정
        oauth2 (OAuth2AuthorizationCodeBearer): OAuth2 설정
    """
    
    keycloak_admin_config: AuthServerConfig
    keycloak_openid: KeycloakOpenID
    oauth2: OAuth2AuthorizationCodeBearer
    
    _keycloak_admin_instance = None
    
    def __init__(self, keycloak_admin_config: AuthServerConfig, keycloak_openid: KeycloakOpenID, oauth2: OAuth2AuthorizationCodeBearer):
        self.keycloak_admin_config = keycloak_admin_config
        self.keycloak_openid = keycloak_openid
        self.oauth2 = oauth2
    
    def _refresh_keycloak_admin_token(self, keycloak_admin: KeycloakAdmin):
        """Keycloak 관리자 토큰을 갱신합니다.

        Args:
            keycloak_admin (KeycloakAdmin): Keycloak 관리자 인스턴스
        """

        try:
            keycloak_admin.connection.refresh_token()
        except KeycloakError as e:
            response_code = getattr(e, "response_code", None) or status.HTTP_500_INTERNAL_SERVER_ERROR
            if response_code >= 500:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="사용자 인증/인가 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.",
                ) from e

            raise

    @property
    def keycloak_admin(self) -> KeycloakAdmin:
        """Keycloak 관리자 인스턴스를 반환합니다.

        Keycloak 관리자 인스턴스가 없는 경우 생성하며, 토큰이 만료된 경우 갱신합니다.

        Returns:
            KeycloakAdmin: Keycloak 관리자 인스턴스
        """
        
        if self._keycloak_admin_instance is None:
            keycloak_admin = KeycloakAdmin(
                server_url=self.keycloak_admin_config.server_url,
                realm_name=self.keycloak_admin_config.realm_name,
                verify=True,
                username=self.keycloak_admin_config.admin_username,
                password=self.keycloak_admin_config.admin_password
            )
            self._refresh_keycloak_admin_token(keycloak_admin)
            self._keycloak_admin_instance = keycloak_admin
        
        # 토큰 만료 확인 및 갱신
        if self._keycloak_admin_instance.connection.expires_at is not None and self._keycloak_admin_instance.connection.expires_at <= datetime.now():
            self._refresh_keycloak_admin_token(self._keycloak_admin_instance)
        return self._keycloak_admin_instance


class TokenUserInfo(BaseModel):
    """토큰 사용자 정보 클래스"""
    
    token: Optional[str] = "-"
    """토큰"""
    auth_server: Optional[str] = None
    """인증 서버 별칭"""
    sub: str
    """사용자 식별 ID"""
    username: Optional[str] = None
    """사용자명"""
    email: Optional[str] = None
    """사용자 이메일"""
    email_verified: Optional[bool] = None
    """이메일 인증 여부"""
    family_name: Optional[str] = None
    """성"""
    given_name: Optional[str] = None
    """이름"""
    name: Optional[str] = None
    """전체 이름"""
    roles: Optional[list[str]] = []
    """권한"""
    groups: Optional[list[str]] = []
    """그룹"""
    attributes: Optional[dict] = {}
    """기타 속성"""
