"""Session cookie:簽章(不是加密),httpOnly、Secure、SameSite=Lax、path 綁自己的前綴。

cookie 只承載 IdP 發的 token,本服務不自簽任何身分憑證(平台鐵則 2)。
每次請求仍會對 access token 做完整 RS256/JWKS 驗證——cookie 簽章只防竄改與跨 App 誤用。
"""

import json
from dataclasses import asdict, dataclass

from fastapi import Response
from itsdangerous import BadSignature, URLSafeSerializer

from .config import Settings

_LOGIN_SALT = "upload-program.login"
_SESSION_SALT = "upload-program.session"


@dataclass(slots=True)
class LoginState:
    """登入往返期間的暫存(PKCE verifier + state + nonce),存在短效 cookie。"""

    state: str
    verifier: str
    nonce: str
    next_path: str = "/"


@dataclass(slots=True)
class SessionData:
    access_token: str
    refresh_token: str = ""
    id_token: str = ""


class CookieCodec:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._login = URLSafeSerializer(settings.session_secret, salt=_LOGIN_SALT)
        self._session = URLSafeSerializer(settings.session_secret, salt=_SESSION_SALT)

    @property
    def login_cookie_name(self) -> str:
        return f"{self._settings.session_cookie_name}_login"

    def _set(self, response: Response, name: str, value: str, max_age: int) -> None:
        response.set_cookie(
            name,
            value,
            max_age=max_age,
            path=self._settings.cookie_path,  # 綁前綴,避免與同主機其他 App 互蓋
            httponly=True,
            secure=self._settings.session_cookie_secure,
            samesite="lax",
        )

    def _clear(self, response: Response, name: str) -> None:
        response.delete_cookie(name, path=self._settings.cookie_path)

    # --- 登入往返 ---

    def set_login_state(self, response: Response, data: LoginState) -> None:
        self._set(response, self.login_cookie_name, self._login.dumps(asdict(data)), 600)

    def read_login_state(self, raw: str | None) -> LoginState | None:
        if not raw:
            return None
        try:
            return LoginState(**self._login.loads(raw))
        except (BadSignature, TypeError, ValueError, json.JSONDecodeError):
            return None

    def clear_login_state(self, response: Response) -> None:
        self._clear(response, self.login_cookie_name)

    # --- 登入後 session ---

    def set_session(self, response: Response, data: SessionData) -> None:
        self._set(
            response,
            self._settings.session_cookie_name,
            self._session.dumps(asdict(data)),
            self._settings.session_max_age_seconds,
        )

    def read_session(self, raw: str | None) -> SessionData | None:
        if not raw:
            return None
        try:
            return SessionData(**self._session.loads(raw))
        except (BadSignature, TypeError, ValueError, json.JSONDecodeError):
            return None

    def clear_session(self, response: Response) -> None:
        self._clear(response, self._settings.session_cookie_name)
