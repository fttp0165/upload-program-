"""T60:OIDC 伺服器端呼叫的內部端點覆寫(portal 施工單 v1.3 §4.4)。

容器內連對外網址不通(sporton_core 與本專案上線當天都實測過),而 discovery
文件裡的 `jwks_uri`/`token_endpoint` 全是對外網址——照著走會繞回連不通的位址。
平台標準做法(契約 v1.10 §2.4):**伺服器發出的請求走內部位址、瀏覽器走的
維持對外、`iss` 是字串比對不是連線**。

這裡釘住的行為:
1. 三個覆寫設定(discovery / token / jwks)生效時,伺服器端呼叫用覆寫值
2. 未覆寫時行為與從前完全相同(向後相容,VM 未改 .env 不得壞)
3. 🔴 瀏覽器端點(authorization / end_session)**永遠**取 discovery 的對外值,
   不受覆寫影響——覆寫了它們,使用者會被導去連不到的內部位址
4. 🔴 issuer 一致性檢查在「從內部位址抓 discovery」的形態下**仍然生效**
   ——文件內的 issuer 是 KC_HOSTNAME 產生的對外值,必須等於設定的 OIDC_ISSUER
"""

import pytest

from app.config import Settings
from app.oidc import OidcClient

_BASE = {
    "public_base_url": "https://catsapp.example.test",
    "api_prefix": "/upload",
    "database_url": "sqlite+aiosqlite:///:memory:",
    "oidc_issuer": "https://auth.example.test/realms/test",
    "oidc_client_id": "c",
    "oidc_client_secret": "not-a-real-secret",
    "session_secret": "not-a-real-session-secret",
    "s3_endpoint_url": "http://minio.invalid:9000",
    "s3_bucket": "b",
    "s3_access_key": "a",
    "s3_secret_key": "s",
}

_INTERNAL = "http://keycloak.internal:8080/auth/realms/test"

# 一份「Keycloak 依 KC_HOSTNAME 產生」形態的 discovery 文件:端點全是對外網址。
_DOC = {
    "issuer": "https://auth.example.test/realms/test",
    "authorization_endpoint": "https://auth.example.test/realms/test/protocol/openid-connect/auth",
    "token_endpoint": "https://auth.example.test/realms/test/protocol/openid-connect/token",
    "jwks_uri": "https://auth.example.test/realms/test/protocol/openid-connect/certs",
    "end_session_endpoint": "https://auth.example.test/realms/test/protocol/openid-connect/logout",
}


def _settings(**overrides) -> Settings:
    return Settings(**{**_BASE, **overrides})


def test_未覆寫時_discovery位址由issuer推導():
    client = OidcClient(_settings())
    assert client.discovery_url == (
        "https://auth.example.test/realms/test/.well-known/openid-configuration"
    )


def test_覆寫時_discovery位址採用覆寫值():
    client = OidcClient(
        _settings(oidc_discovery_url=f"{_INTERNAL}/.well-known/openid-configuration")
    )
    assert client.discovery_url == f"{_INTERNAL}/.well-known/openid-configuration"


def test_未覆寫時_端點取discovery值_行為不變():
    client = OidcClient(_settings())
    d = client._build_discovery(dict(_DOC))
    assert d.token_endpoint == _DOC["token_endpoint"]
    assert d.jwks_uri == _DOC["jwks_uri"]


def test_覆寫時_token與jwks採用內部位址():
    client = OidcClient(
        _settings(
            oidc_token_url=f"{_INTERNAL}/protocol/openid-connect/token",
            oidc_jwks_url=f"{_INTERNAL}/protocol/openid-connect/certs",
        )
    )
    d = client._build_discovery(dict(_DOC))
    assert d.token_endpoint == f"{_INTERNAL}/protocol/openid-connect/token"
    assert d.jwks_uri == f"{_INTERNAL}/protocol/openid-connect/certs"


def test_瀏覽器端點永遠取discovery的對外值():
    """🔴 authorization / end_session 是瀏覽器要去的地方,覆寫設定不得影響它們。"""
    client = OidcClient(
        _settings(
            oidc_discovery_url=f"{_INTERNAL}/.well-known/openid-configuration",
            oidc_token_url=f"{_INTERNAL}/protocol/openid-connect/token",
            oidc_jwks_url=f"{_INTERNAL}/protocol/openid-connect/certs",
        )
    )
    d = client._build_discovery(dict(_DOC))
    assert d.authorization_endpoint == _DOC["authorization_endpoint"]
    assert d.end_session_endpoint == _DOC["end_session_endpoint"]


def test_issuer檢查在內部抓取形態下仍然生效():
    """🔴 從內部位址抓 discovery,文件內 issuer 仍須等於設定的對外 OIDC_ISSUER。

    這條檢查擋的是「接錯 realm」;內部抓取不是放寬它的理由。
    """
    client = OidcClient(
        _settings(oidc_discovery_url=f"{_INTERNAL}/.well-known/openid-configuration")
    )
    bad = {**_DOC, "issuer": "https://auth.example.test/realms/other"}
    with pytest.raises(RuntimeError):
        client._build_discovery(bad)


def test_issuer預期值不受覆寫影響():
    """🔴 §4.4:`iss` 是字串比對非連線;覆寫內部端點後,驗 token 的預期 iss
    仍是設定的對外 OIDC_ISSUER(把 iss 改成內部位址=每個 token 都驗失敗)。"""
    settings = _settings(
        oidc_token_url=f"{_INTERNAL}/protocol/openid-connect/token",
        oidc_jwks_url=f"{_INTERNAL}/protocol/openid-connect/certs",
    )
    assert settings.oidc_issuer == "https://auth.example.test/realms/test"
    client = OidcClient(settings)
    d = client._build_discovery(dict(_DOC))
    assert d.issuer == "https://auth.example.test/realms/test"
