"""Token 驗證的契約測試(SSO 接入契約 §3.2)。

其他測試檔為了跑得快用 `FakeOidc` 繞過驗章,結果是 `app/oidc.py` 裡**真正的驗證邏輯完全沒被測到**
——而那正是契約義務所在。本檔用**當場產生的 RSA 金鑰**驗真章,把以下五條釘住:

- 只接受 **RS256**;HS256 / `alg=none` 一律拒
- 驗 `iss`、`aud`、`exp`、簽章
- 時鐘容忍 **±30 秒**
- 支援 **`kid` 輪替**(新舊金鑰並存時靠 kid 選鍵,不能假設只有一把)
- 驗失敗一律 **401**(不是 403)

🔴 契約 §4.8:一律假資料。本檔的金鑰是每次執行現場產生的測試金鑰,不是任何真實環境的金鑰。
"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings
from app.oidc import OidcClient
from app.problems import ProblemError

ISSUER = "https://auth.example.test/realms/test"
CLIENT_ID = "upload-program-test"


def _settings() -> Settings:
    return Settings(
        public_base_url="https://catsapp.example.test",
        api_prefix="/upload",
        database_url="sqlite+aiosqlite:///:memory:",
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret="not-a-real-secret",
        session_secret="not-a-real-session-secret",
        s3_endpoint_url="http://minio.invalid:9000",
        s3_bucket="b",
        s3_access_key="a",
        s3_secret_key="s",
    )


class _FakeJwks:
    """假的 JWKS client:依 token 標頭的 kid 選鍵,行為對齊 PyJWKClient 的 kid 輪替。"""

    def __init__(self, keys: dict[str, object]) -> None:
        self.keys = keys

    def get_signing_key_from_jwt(self, token: str):
        kid = jwt.get_unverified_header(token).get("kid")
        if kid not in self.keys:
            raise LookupError(f"unknown kid: {kid}")
        return type("Key", (), {"key": self.keys[kid].public_key()})()


@pytest.fixture
def keys() -> dict:
    """兩把金鑰,模擬輪替期間新舊並存。"""
    return {
        "kid-old": rsa.generate_private_key(public_exponent=65537, key_size=2048),
        "kid-new": rsa.generate_private_key(public_exponent=65537, key_size=2048),
    }


@pytest.fixture
def client(keys) -> OidcClient:
    oidc = OidcClient(_settings())
    oidc._jwks = _FakeJwks(keys)
    return oidc


def _token(keys, kid="kid-new", alg="RS256", **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "test-sub-0001",
        "aud": CLIENT_ID,
        "iat": now,
        "exp": now + 300,
        **overrides,
    }
    # HS256 分支的假密鑰刻意給足 32 bytes,只是為了不讓 PyJWT 的長度警告混淆測試輸出
    key = keys[kid] if alg == "RS256" else "symmetric-secret-for-tests-only-32b"
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def test_正常RS256token通過(client, keys):
    claims = client.verify(_token(keys))
    assert claims["sub"] == "test-sub-0001"


def test_kid輪替時新舊金鑰都驗得過(client, keys):
    # 🔴 契約 §3.2:「別假設只有一把」——金鑰換發期間舊 kid 簽的 token 仍在流通
    assert client.verify(_token(keys, kid="kid-new"))["sub"] == "test-sub-0001"
    assert client.verify(_token(keys, kid="kid-old"))["sub"] == "test-sub-0001"


def test_未知kid被拒401(client, keys):
    token = jwt.encode(
        {"iss": ISSUER, "sub": "x", "aud": CLIENT_ID, "iat": 0, "exp": 9999999999},
        keys["kid-new"],
        algorithm="RS256",
        headers={"kid": "kid-不存在"},
    )
    with pytest.raises(ProblemError) as exc:
        client.verify(token)
    assert exc.value.status_code == 401


def test_HS256一律拒(client, keys):
    """對稱簽章在多服務環境是壞主意;若接受 HS256,拿得到 client secret 的人就能偽造身分。"""
    with pytest.raises(ProblemError) as exc:
        client.verify(_token(keys, alg="HS256"))
    assert exc.value.status_code == 401
    assert "HS256" in exc.value.detail


def test_alg_none一律拒(client):
    token = jwt.encode(
        {"iss": ISSUER, "sub": "x", "aud": CLIENT_ID, "iat": 0, "exp": 9999999999},
        key="",
        algorithm="none",
        headers={"kid": "kid-new"},
    )
    with pytest.raises(ProblemError) as exc:
        client.verify(token)
    assert exc.value.status_code == 401


def test_過期token被拒(client, keys):
    now = int(time.time())
    with pytest.raises(ProblemError) as exc:
        client.verify(_token(keys, iat=now - 600, exp=now - 300))
    assert exc.value.status_code == 401
    assert "過期" in exc.value.detail


def test_時鐘容忍30秒(client, keys):
    """契約已定:±30 秒。剛過期 10 秒仍應通過,過期 120 秒則不行。"""
    now = int(time.time())
    assert client.verify(_token(keys, iat=now - 300, exp=now - 10))["sub"] == "test-sub-0001"
    with pytest.raises(ProblemError):
        client.verify(_token(keys, iat=now - 300, exp=now - 120))


def test_別人的aud被拒(client, keys):
    """防止拿 A app 的 token 打 B app(契約 §3.1)。"""
    with pytest.raises(ProblemError) as exc:
        client.verify(_token(keys, aud="another-app"))
    assert exc.value.status_code == 401
    assert "aud" in exc.value.detail


def test_iss不符被拒(client, keys):
    with pytest.raises(ProblemError) as exc:
        client.verify(_token(keys, iss="https://evil.example.test/realms/fake"))
    assert exc.value.status_code == 401


def test_缺sub被拒(client, keys):
    now = int(time.time())
    token = jwt.encode(
        {"iss": ISSUER, "aud": CLIENT_ID, "iat": now, "exp": now + 300},
        keys["kid-new"],
        algorithm="RS256",
        headers={"kid": "kid-new"},
    )
    with pytest.raises(ProblemError) as exc:
        client.verify(token)
    assert exc.value.status_code == 401


def test_nonce不符被拒(client, keys):
    """ID token 的重放防護。"""
    with pytest.raises(ProblemError) as exc:
        client.verify(_token(keys, nonce="real-nonce"), expected_nonce="different-nonce")
    assert exc.value.status_code == 401


def test_access_token以azp辨識本服務(client, keys):
    """Keycloak 的 access token 常把 aud 設成 account,client_id 落在 azp。"""
    claims = client.verify_access_token(_token(keys, aud="account", azp=CLIENT_ID))
    assert claims["sub"] == "test-sub-0001"


def test_access_token的azp不是本服務就拒(client, keys):
    with pytest.raises(ProblemError) as exc:
        client.verify_access_token(_token(keys, aud="account", azp="another-app"))
    assert exc.value.status_code == 401


def test_竄改內容後簽章不符(client, keys):
    token = _token(keys)
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload[:-4]}AAAA.{signature}"
    with pytest.raises(ProblemError) as exc:
        client.verify(tampered)
    assert exc.value.status_code == 401


def test_jwks未就緒時回401而非500(keys):
    """IdP 還沒起來時,呼叫端該收到 401 重新登入,不是一個 500。"""
    oidc = OidcClient(_settings())  # 未載入 discovery,_jwks 仍為 None
    with pytest.raises(ProblemError) as exc:
        oidc.verify(_token(keys))
    assert exc.value.status_code == 401
