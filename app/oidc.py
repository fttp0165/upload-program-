"""OIDC 接入(Keycloak)。

依帳號系統接入契約:
- 端點一律從 **Discovery** 動態取,不寫死路徑(§2)
- 流程 = Authorization Code + **PKCE**;禁 implicit、禁 HS256(§0、§4.1)
- 只接受 **RS256**;JWKS 快取 1h、**支援 kid 輪替**;驗 iss/aud/exp/簽章,±30s(§3.2)
- 驗失敗一律 **401**(403 保留給「已認證但未開通/無權限」)
"""

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient

from .config import Settings
from .problems import unauthorized

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    userinfo_endpoint: str = ""
    end_session_endpoint: str = ""
    fetched_at: float = field(default_factory=time.monotonic)


def make_pkce() -> tuple[str, str]:
    """回傳 (code_verifier, code_challenge);challenge method 固定 S256。"""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def _error_code(resp: httpx.Response) -> str:
    """從 token 端點的錯誤回應取出 `error` 碼;取不到回空字串。

    🔴 **只取 `error`,不取 `error_description`。** 前者是列舉值
    (`invalid_grant` / `invalid_client` / …),內容可預期;後者是自由字串,
    由 IdP 決定內容,我方無法保證裡面不會出現使用者名稱、email 或請求片段。
    診斷需要的是前者——知道是 `invalid_grant` 就知道往哪查了;
    後者的邊際價值遠低於「log 不記個資」這條紅線的風險。

    🔴 **解析失敗要安靜降級。** IdP 或 gateway 可能回 HTML 錯誤頁,
    記 log 這件事本身不能變成新的例外(那會讓真正的錯誤被蓋掉——T50 踩過)。

    參數:resp 非 200 的回應。回傳:錯誤碼或空字串。副作用:無。
    """
    try:
        value = resp.json().get("error", "")
    except (ValueError, AttributeError):
        return ""
    return value if isinstance(value, str) else ""


class OidcClient:
    """Discovery + 授權碼交換 + token 驗證。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._discovery: Discovery | None = None
        self._jwks: PyJWKClient | None = None

    # --- Discovery -------------------------------------------------------

    @property
    def discovery_url(self) -> str:
        # T60:覆寫值是給「容器內連不到對外網址」的部署形態走內部位址用的
        return (
            self._settings.oidc_discovery_url
            or f"{self._settings.oidc_issuer}/.well-known/openid-configuration"
        )

    def _build_discovery(self, doc: dict[str, Any]) -> Discovery:
        """把 discovery 文件解析成端點集,並套用伺服器端的內部位址覆寫(T60)。

        🔴 覆寫只及於**伺服器發出請求**的端點(token、jwks);
        authorization / end_session 是瀏覽器要去的地方,永遠取文件裡的對外值。
        🔴 issuer 一致性檢查不因「從內部位址抓文件」而放寬——文件內的 issuer
        是 KC_HOSTNAME 產生的對外值,不等於設定值就是接錯 realm。
        """
        if doc.get("issuer", "").rstrip("/") != self._settings.oidc_issuer:
            raise RuntimeError(
                f"discovery 的 issuer({doc.get('issuer')})與設定的 OIDC_ISSUER 不符"
            )
        return Discovery(
            issuer=doc["issuer"].rstrip("/"),
            authorization_endpoint=doc["authorization_endpoint"],
            token_endpoint=self._settings.oidc_token_url or doc["token_endpoint"],
            jwks_uri=self._settings.oidc_jwks_url or doc["jwks_uri"],
            userinfo_endpoint=doc.get("userinfo_endpoint", ""),
            end_session_endpoint=doc.get("end_session_endpoint", ""),
        )

    async def load_discovery(self, force: bool = False) -> Discovery:
        if self._discovery is not None and not force:
            return self._discovery
        async with httpx.AsyncClient(timeout=self._settings.oidc_http_timeout_seconds) as client:
            resp = await client.get(self.discovery_url)
            resp.raise_for_status()
            doc: dict[str, Any] = resp.json()

        discovery = self._build_discovery(doc)
        self._discovery = discovery
        # PyJWKClient 自帶 kid 對應與快取:金鑰輪替期間新舊並存也能選對鍵。
        self._jwks = PyJWKClient(
            discovery.jwks_uri,
            cache_keys=True,
            lifespan=self._settings.jwks_cache_seconds,
        )
        log.info("oidc discovery 載入完成", extra={"issuer": discovery.issuer})
        return discovery

    @property
    def ready(self) -> bool:
        return self._discovery is not None and self._jwks is not None

    # --- 登入流程 ---------------------------------------------------------

    def authorization_url(
        self,
        discovery: Discovery,
        state: str,
        challenge: str,
        nonce: str,
        prompt: str | None = None,
    ) -> str:
        params = {
            "response_type": "code",  # 🔴 禁 implicit
            "client_id": self._settings.oidc_client_id,
            "redirect_uri": self._settings.oidc_redirect_uri,
            "scope": self._settings.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        # T64:prompt=none = 靜默探測——IdP 有 session 就發 code,沒有就回
        # login_required,全程不顯示登入畫面。流程仍是 code + PKCE,契約 §4.1 不變。
        if prompt:
            params["prompt"] = prompt
        return str(httpx.URL(discovery.authorization_endpoint, params=params))

    async def exchange_code(self, code: str, verifier: str) -> dict[str, Any]:
        discovery = await self.load_discovery()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._settings.oidc_redirect_uri,
            "client_id": self._settings.oidc_client_id,
            "client_secret": self._settings.oidc_client_secret,
            "code_verifier": verifier,
        }
        async with httpx.AsyncClient(timeout=self._settings.oidc_http_timeout_seconds) as client:
            resp = await client.post(discovery.token_endpoint, data=data)
        if resp.status_code != 200:
            # 🔴 不把 IdP 回應原文吐給呼叫端——錯誤碼是**維運資訊**,不是給瀏覽器看的
            #    (洩漏 client 設定細節無益)。使用者看到的訊息維持不變。
            # T87:但 `error` 要記進 log。原本這裡寫「不記(可能含 token)」是過度保守:
            #    token 端點的**錯誤**回應依 RFC 6749 §5.2 只有 error / error_description /
            #    error_uri,不會有 token(有 token 的是 200 那條路徑,而它本來就不記)。
            #    少了這個欄位,invalid_grant 與 invalid_client 在 log 裡長得一模一樣——
            #    兩次登入故障的排查都因此只能靠猜。
            log.warning(
                "授權碼交換失敗",
                extra={"status": resp.status_code, "oidc_error": _error_code(resp)},
            )
            raise unauthorized("授權碼交換失敗,請重新登入。")
        return resp.json()

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        discovery = await self.load_discovery()
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._settings.oidc_client_id,
            "client_secret": self._settings.oidc_client_secret,
        }
        async with httpx.AsyncClient(timeout=self._settings.oidc_http_timeout_seconds) as client:
            resp = await client.post(discovery.token_endpoint, data=data)
        if resp.status_code != 200:
            # T87:續期原本完全沒有 log——失敗時同樣查無可查。
            log.warning(
                "續期失敗",
                extra={"status": resp.status_code, "oidc_error": _error_code(resp)},
            )
            raise unauthorized("session 已過期,請重新登入。")
        return resp.json()

    def logout_url(self, discovery: Discovery, id_token: str | None) -> str:
        """RP-initiated single logout;不得只清本地 session 假裝登出(契約 §4.5)。"""
        target = f"{self._settings.external_base}{self._settings.post_logout_path}"
        if not discovery.end_session_endpoint:
            return target
        params: dict[str, str] = {"post_logout_redirect_uri": target}
        if id_token:
            params["id_token_hint"] = id_token
        else:
            params["client_id"] = self._settings.oidc_client_id
        return str(httpx.URL(discovery.end_session_endpoint, params=params))

    # --- token 驗證 -------------------------------------------------------

    def verify(self, token: str, *, expected_nonce: str | None = None) -> dict[str, Any]:
        """驗 access token / ID token。失敗一律拋 401。"""
        if self._jwks is None:
            raise unauthorized("身分服務尚未就緒,請稍後再試。")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise unauthorized("token 格式無效") from None

        # 🔴 只接受 RS256;alg=none / HS256 一律拒(契約 §3.2)。
        if header.get("alg") != "RS256":
            raise unauthorized(f"不接受的簽章演算法:{header.get('alg')}")

        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
        except Exception:  # PyJWKClient 會丟自己的錯誤型別
            raise unauthorized("找不到對應的簽章金鑰(kid)") from None

        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._settings.oidc_client_id,
                issuer=self._settings.oidc_issuer,
                leeway=self._settings.clock_skew_seconds,
                options={"require": ["exp", "iat", "sub", "iss"]},
            )
        except jwt.ExpiredSignatureError:
            raise unauthorized("token 已過期") from None
        except jwt.InvalidAudienceError:
            # 防拿 A app 的 token 打 B app(契約 §3.1)。
            raise unauthorized("token 的 aud 不是本服務") from None
        except jwt.PyJWTError as exc:
            raise unauthorized(f"token 驗證失敗:{type(exc).__name__}") from None

        if expected_nonce is not None and claims.get("nonce") != expected_nonce:
            raise unauthorized("nonce 不符,可能是重放攻擊")
        return claims

    def verify_access_token(self, token: str) -> dict[str, Any]:
        """Keycloak 的 access token 有時 aud 放 account,azp 才是 client_id。"""
        try:
            return self.verify(token)
        except Exception:
            claims = self._verify_ignoring_audience(token)
            if claims.get("azp") != self._settings.oidc_client_id:
                raise unauthorized("token 的 aud/azp 不是本服務") from None
            return claims

    def _verify_ignoring_audience(self, token: str) -> dict[str, Any]:
        if self._jwks is None:
            raise unauthorized("身分服務尚未就緒,請稍後再試。")
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise unauthorized(f"不接受的簽章演算法:{header.get('alg')}")
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self._settings.oidc_issuer,
                leeway=self._settings.clock_skew_seconds,
                options={"require": ["exp", "iat", "sub", "iss"], "verify_aud": False},
            )
        except jwt.PyJWTError:
            raise unauthorized("token 驗證失敗") from None
