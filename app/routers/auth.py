"""登入 / 登出(OIDC Authorization Code + PKCE)。

注意路徑:本服務被掛在 gateway 的 `/«PREFIX»/` 底下且前綴會被剝掉,
所以這裡註冊的是 `/oidc/callback/`,對外實際網址是
`https://catsapp.sporton.com.tw/«PREFIX»/oidc/callback/`——申請 client 時要登記後者。
"""

import logging
import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from .. import problems
from ..config import Settings
from ..db import get_session
from ..models import UserStatus
from ..oidc import make_pkce
from ..security import upsert_user, verified_email
from ..session import LoginState, SessionData

router = APIRouter(tags=["auth"])
log = logging.getLogger(__name__)


def _safe_next(raw: str | None) -> str:
    """只允許站內相對路徑,擋開放轉址。"""
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/"
    return raw


@router.get("/auth/login", summary="導向 IdP 登入頁(Auth Code + PKCE)")
async def login(
    request: Request,
    next: Annotated[str | None, Query()] = None,
    silent: Annotated[bool, Query()] = False,
):
    # T64:silent=1 走 prompt=none 靜默探測——portal 登入過的人免再按登入;
    # 沒有 IdP session 的人由 callback 無聲送回落地頁,不會看到 Keycloak 畫面。
    oidc = request.app.state.oidc
    try:
        discovery = await oidc.load_discovery()
    except Exception as exc:
        log.error("無法取得 OIDC discovery", extra={"error": type(exc).__name__})
        raise problems.service_unavailable("身分服務暫時無法連線,請稍後再試。") from None

    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(16)

    response = RedirectResponse(
        oidc.authorization_url(
            discovery, state, challenge, nonce, prompt="none" if silent else None
        ),
        status_code=302,
    )
    codec = request.app.state.cookies
    codec.set_login_state(
        response,
        LoginState(
            state=state,
            verifier=verifier,
            nonce=nonce,
            next_path=_safe_next(next),
            silent=silent,
        ),
    )
    if silent:
        # 🔴 防迴圈:發起當下就種探測 cookie(5 分鐘)——就算 callback 沒回來
        # (IdP 異常、使用者中途關頁),首頁也不會再連續發起探測。
        codec.set_sso_probe(response)
    return response


@router.get("/oidc/callback/", summary="IdP 導回;換 token、建/取本地 user")
async def callback(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
):
    settings: Settings = request.app.state.settings
    codec = request.app.state.cookies
    oidc = request.app.state.oidc

    login_state = codec.read_login_state(request.cookies.get(codec.login_cookie_name))

    if error:
        # T64:靜默探測的 login_required 系列=「這個瀏覽器沒有 IdP session」,
        # 是預期結果不是錯誤——無聲送回原頁(落地頁),不記 warning、不 401。
        if (
            login_state is not None
            and login_state.silent
            and error in {"login_required", "interaction_required", "consent_required"}
        ):
            response = RedirectResponse(
                f"{settings.external_base}{login_state.next_path}", status_code=302
            )
            codec.clear_login_state(response)
            return response
        log.warning("IdP 回傳錯誤", extra={"oidc_error": error})
        raise problems.unauthorized("登入未完成,請重試。")
    if not code or not state:
        raise problems.bad_request("callback 缺少 code 或 state")
    if login_state is None:
        raise problems.unauthorized("登入流程已逾時,請重新登入。")
    if not secrets.compare_digest(login_state.state, state):
        raise problems.unauthorized("state 不符,登入請求可能遭竄改。")

    tokens = await oidc.exchange_code(code, login_state.verifier)
    access_token = tokens.get("access_token", "")
    id_token = tokens.get("id_token", "")
    if not access_token:
        raise problems.unauthorized("IdP 未回傳 access token")

    # ID token 驗 nonce;access token 之後每次請求都會再驗一次。
    if id_token:
        oidc.verify(id_token, expected_nonce=login_state.nonce)
    claims = oidc.verify_access_token(access_token)

    sub = claims.get("sub")
    if not sub:
        raise problems.unauthorized("token 缺少 sub")

    # §4.2a:快取來源**僅限 name claim**(裁決原文;preferred_username 不在准許範圍)。
    user = await upsert_user(
        session,
        sub,
        settings,
        display_name=claims.get("name"),
        notify_email=verified_email(claims),  # T99 / L1b:只信 email_verified=true
    )

    # 🔴 T81 迴圈防線:停用者**不建立 session**,當場送回平台入口。
    #
    # 根本原因:`optional_identity` 對 disabled 會吞掉 403 回 None——網頁層看不出
    # 「停用」與「匿名」的差別。T81 讓首頁把匿名瀏覽器送去登入之後,停用者若拿得到
    # session,就會 首頁 → IdP → callback → 首頁 → IdP … 永遠繞下去(IdP 那邊有
    # session,每一圈都是無聲的,使用者只會看到瀏覽器一直轉)。
    # 擋在這裡而不是首頁,是因為「被停用的帳號本來就不該有 session」——
    # 這件事跟首頁怎麼導流無關,即使日後改回落地頁也應該成立。
    if user.status is UserStatus.disabled:
        log.info("停用帳號登入被拒", extra={"user_id": str(user.id)})
        response = RedirectResponse(settings.portal_home_url, status_code=302)
        codec.clear_login_state(response)
        return response

    user.last_login_at = datetime.now(UTC)
    await session.commit()

    target = f"{settings.external_base}{login_state.next_path}"
    response = RedirectResponse(target, status_code=302)
    codec.clear_login_state(response)
    codec.set_session(
        response,
        SessionData(
            access_token=access_token,
            refresh_token=tokens.get("refresh_token", ""),
            id_token=id_token,
        ),
    )
    log.info("使用者登入", extra={"user_id": str(user.id), "status": user.status.value})
    return response


@router.get(
    "/oidc/frontchannel-logout",
    status_code=204,
    summary="front-channel logout:IdP 登出時以 iframe 呼叫,刪我方 cookie",
)
async def frontchannel_logout(request: Request) -> Response:
    """SSO 契約 v2.0 §10.3 的 front-channel logout 端點(T74)。

    **要解決的事**:我方 session 是自己簽的 cookie(這是契約 §10.1 認可的設計,
    不該「修掉」——直接轉發 IdP token 會讓後台移除角色也擋不住)。代價是
    IdP 結束 session 時我們不知情:A 在入口登出、B 登入後,進本站**仍然是 A**。
    共用電腦上那是安全事故,所以 IdP 的登出頁會以 iframe 載入本端點來刪 cookie。

    🔴 **免認證**:iframe 載入時不會帶我方 token;要求認證等於這個端點永遠不生效。
    🔴 **只准刪 cookie**——不建 session、不寫業務資料、不寫稽核。
       被任意第三方呼叫的最壞後果必須只是「使用者被登出」。
    🔴 **冪等**:本來就沒有 session 也回 204(iframe 不看內容,狀態碼要乾淨)。
    🔴 刪除用的 `Path` 由 `CookieCodec` 統一帶上,與種下時相同——
       Path 不符的話瀏覽器根本不會刪,那正是契約緣起段「看起來有設、實際不做事」的死法。

    參數:Keycloak 會帶 `iss` 與 `sid`,**刻意不宣告也不使用**——我方 session
    無狀態,無從比對 `sid`;帶與不帶都必須能處理(§10.3)。
    回傳:204,無內容。副作用:僅清除本服務的 cookie。
    """
    response = Response(status_code=204)
    codec = request.app.state.cookies
    codec.clear_session(response)
    codec.clear_login_state(response)
    return response


@router.post("/auth/refresh", summary="用 refresh token 換新的 access token")
async def refresh(request: Request):
    settings: Settings = request.app.state.settings
    codec = request.app.state.cookies
    data = codec.read_session(request.cookies.get(settings.session_cookie_name))
    if data is None or not data.refresh_token:
        raise problems.unauthorized("沒有可用的 session,請重新登入。")

    tokens = await request.app.state.oidc.refresh(data.refresh_token)
    response = JSONResponse({"status": "refreshed"})
    codec.set_session(
        response,
        SessionData(
            access_token=tokens.get("access_token", ""),
            refresh_token=tokens.get("refresh_token", data.refresh_token),
            id_token=tokens.get("id_token", data.id_token),
        ),
    )
    return response


@router.get("/auth/logout", summary="single logout:清本地 session 並導向 IdP 登出")
async def logout(request: Request):
    """🔴 契約 §4.5:不得只清本地 session 假裝登出,一定要導 IdP 的 logout 端點。"""
    settings: Settings = request.app.state.settings
    codec = request.app.state.cookies
    data = codec.read_session(request.cookies.get(settings.session_cookie_name))

    try:
        discovery = await request.app.state.oidc.load_discovery()
        target = request.app.state.oidc.logout_url(discovery, data.id_token if data else None)
    except Exception:
        target = f"{settings.external_base}{settings.post_logout_path}"

    response = RedirectResponse(target, status_code=302)
    codec.clear_session(response)
    codec.clear_login_state(response)
    return response
