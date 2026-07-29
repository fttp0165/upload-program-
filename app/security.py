"""身分與授權相依。

契約要點:
- 身分鍵一律是 JWT 的 `sub`;首登自動建**零角色**本地 user(§4.3)
- deny-by-default:未開通 → **403 待開通**(不是 401,也不是冷 Forbidden)
- 派角色只在本服務後台,不碰 Keycloak(§4.4)
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import problems
from .audit import AuditAction, record
from .config import Settings, get_settings
from .db import get_session
from .logging_setup import user_id_var
from .models import (
    PlatformRole,
    Project,
    ProjectMember,
    ProjectRole,
    User,
    UserStatus,
    Visibility,
)
from .oidc import OidcClient
from .quota import project_limit
from .schemas import ProjectOut
from .session import SessionData

_ROLE_RANK = {ProjectRole.viewer: 0, ProjectRole.maintainer: 1, ProjectRole.owner: 2}


@dataclass(slots=True)
class Identity:
    """通過驗證的身分。email/姓名只在記憶體中傳遞供顯示,**絕不寫進業務庫**。"""

    user: User
    claims: dict

    @property
    def sub(self) -> str:
        return self.user.sub

    @property
    def display_email(self) -> str | None:
        return self.claims.get("email")

    @property
    def display_name(self) -> str | None:
        return self.claims.get("name") or self.claims.get("preferred_username")


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


# 續期後的新 session 暫存在這個 request.state 欄位,由中介層在回應產生後寫成 cookie。
RENEWED_SESSION_ATTR = "renewed_session"


async def _session_token(request: Request) -> str | None:
    """從 session cookie 取 access token,**必要時自動向 IdP 續期**(T52)。

    🐛 為什麼需要自動續期:SSO 契約 §3.3 把 access token 壓到 **300 秒**,
    但本服務的網頁是伺服器端算繪、全站零 JS,沒有人會去打 `POST /auth/refresh`;
    session cookie 卻活 10 小時。不續期的話,登入 5 分鐘後所有頁面都會靜默
    退回「請先登入」,而伺服器沒有任何錯誤。

    🔴 **續期不得繞過收權**:契約把 access token 壓到 300 秒的目的,正是
    「管理員收權 / IdP 停用帳號後,既發 token 最長只再活 5 分鐘」。所以這裡是
    **真的去問 IdP**(refresh_token grant)——IdP 那邊帳號被停用時 refresh 會失敗,
    使用者立刻被登出。自行延長任何東西都會把這個保護拆掉。
    (本地的 `user.status is disabled` 檢查在 `get_identity` 裡,兩層都保留。)

    參數:request。回傳:可用的 access token 或 None。
    副作用:可能呼叫 IdP 換發 token,並把新 session 暫存到 `request.state`
    (由 `SessionRenewalMiddleware` 寫回 cookie——相依注入階段還沒有 response 物件)。
    """
    codec = request.app.state.cookies
    settings: Settings = request.app.state.settings
    data: SessionData | None = codec.read_session(request.cookies.get(settings.session_cookie_name))
    if data is None or not data.access_token:
        return None

    oidc: OidcClient = request.app.state.oidc
    try:
        oidc.verify_access_token(data.access_token)
        return data.access_token  # 還有效就直接用,不要每次請求都去打 IdP
    except problems.ProblemError:
        pass

    if not data.refresh_token:
        return None

    try:
        tokens = await oidc.refresh(data.refresh_token)
    except Exception:
        # refresh 也失效(IdP 停用帳號、refresh 過期…)→ 視為未登入,由呼叫端決定怎麼呈現。
        return None

    renewed = SessionData(
        access_token=tokens.get("access_token", ""),
        refresh_token=tokens.get("refresh_token", data.refresh_token),
        id_token=tokens.get("id_token", data.id_token),
    )
    if not renewed.access_token:
        return None
    setattr(request.state, RENEWED_SESSION_ATTR, renewed)
    return renewed.access_token


async def upsert_user(
    session: AsyncSession, sub: str, settings: Settings, display_name: str | None = None
) -> User:
    """首登自動建 user:狀態 pending、零角色。之後由本服務管理員開通。

    副作用:首登建立資料列;既有 pending 帳號在 bootstrap 清單內時升級並寫稽核;
    T59(契約 §4.2a):每次登入以本人 token 的 `name` claim 覆寫顯示名稱快取
    (claim 不存在 → 寫 NULL,畫面 fallback 到 sub)。
    """
    user = (await session.execute(select(User).where(User.sub == sub))).scalar_one_or_none()
    if user is not None:
        # §4.2a:每次登入覆寫(含覆寫成 NULL——IdP 拿掉名字,快取不得留舊值)。
        if user.display_name_cache != display_name:
            user.display_name_cache = display_name
            await session.commit()
            await session.refresh(user)
        # 🐛 首次上線實測(2026-07-29):bootstrap 原本只在**建號時**生效,但 T45 的
        # 「先部署、後拿 sub」自助流程在結構上保證了管理員的帳號會先以 pending 存在
        # ——要看到自己的 sub 就得先登入。結果清單永遠升不了級,第一個管理員只能
        # 手打 SQL 解鎖。改為:既有帳號在清單內且**仍為 pending**時,登入當下升級。
        # 🔴 只升 pending:disabled 是被刻意停權的(清單不是繞過停權的後門);
        # active 成員的角色調整是管理後台的職權(清單只解「第一個管理員」的死結,
        # 不該成為藏在環境變數裡、稽核看不見操作者的第二條派角色路)。
        if user.status is UserStatus.pending and sub in settings.bootstrap_admins:
            user.status = UserStatus.active
            user.platform_role = PlatformRole.admin
            user.activated_at = datetime.now(UTC)
            # 開通就是開通,不因操作者是系統就不留稽核;actor 為空 = 系統動作。
            record(
                session,
                action=AuditAction.user_activate,
                actor_id=None,
                target_type="user",
                target_id=user.id,
            )
            await session.commit()
            await session.refresh(user)
        return user

    is_bootstrap = sub in settings.bootstrap_admins
    user = User(
        sub=sub,
        display_name_cache=display_name,
        status=UserStatus.active if is_bootstrap else UserStatus.pending,
        platform_role=PlatformRole.admin if is_bootstrap else PlatformRole.member,
        activated_at=datetime.now(UTC) if is_bootstrap else None,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        # 同一人並發首登:另一個請求先建好了,讀回來即可。
        await session.rollback()
        user = (await session.execute(select(User).where(User.sub == sub))).scalar_one()
    await session.refresh(user)
    return user


async def get_identity(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Identity:
    """驗 token → 取 sub → 對應本地 user。token 有問題一律 401。"""
    token = _bearer_token(request) or await _session_token(request)
    if not token:
        raise problems.unauthorized("缺少憑證:請帶 Authorization: Bearer,或先登入。")

    oidc: OidcClient = request.app.state.oidc
    claims = oidc.verify_access_token(token)
    sub = claims.get("sub")
    if not sub:
        raise problems.unauthorized("token 缺少 sub")

    settings: Settings = request.app.state.settings
    # §4.2a:快取來源**僅限 name claim**(裁決原文;preferred_username 不在准許範圍)。
    user = await upsert_user(session, sub, settings, display_name=claims.get("name"))
    if user.status is UserStatus.disabled:
        raise problems.forbidden("此帳號已被停用,請聯絡平台管理員。")

    user_id_var.set(str(user.id))
    return Identity(user=user, claims=claims)


async def require_active(identity: Annotated[Identity, Depends(get_identity)]) -> Identity:
    """已認證但未開通 → 403 待開通(deny-by-default)。"""
    if not identity.user.is_active:
        raise problems.pending_activation()
    return identity


async def require_admin(identity: Annotated[Identity, Depends(require_active)]) -> Identity:
    if not identity.user.is_admin:
        raise problems.forbidden("需要平台管理員權限。")
    return identity


async def optional_identity(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Identity | None:
    """取身分,**取不到就回 None 而不是拋錯**——給網頁用的(T40)。

    網頁跟 API 不同:匿名訪客開首頁該看到登入按鈕,不是一頁 401;
    待開通或被停用的人也該看得到導航列(頁面內容自己再決定要不要擋)。

    參數:request、session。回傳:Identity 或 None。副作用:首登會建立本地 user
    (與 `get_identity` 相同)。
    """
    try:
        return await get_identity(request, session)
    except problems.ProblemError:
        return None


CurrentUser = Annotated[Identity, Depends(require_active)]
OptionalUser = Annotated[Identity | None, Depends(optional_identity)]
AdminUser = Annotated[Identity, Depends(require_admin)]
DbSession = Annotated[AsyncSession, Depends(get_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]


# --- 專案層授權 -------------------------------------------------------------


async def project_role(
    session: AsyncSession, project: Project, user: User
) -> ProjectRole | None:
    if project.owner_id == user.id:
        return ProjectRole.owner
    stmt = select(ProjectMember).where(
        ProjectMember.project_id == project.id, ProjectMember.user_id == user.id
    )
    member = (await session.execute(stmt)).scalar_one_or_none()
    return member.role if member else None


async def get_project(session: AsyncSession, slug: str) -> Project:
    project = (
        await session.execute(select(Project).where(Project.slug == slug))
    ).scalar_one_or_none()
    if project is None:
        raise problems.not_found(f"找不到專案 {slug}")
    return project


async def require_project_read(
    session: AsyncSession, project: Project, identity: Identity
) -> ProjectRole | None:
    """internal 專案所有已開通者可讀;private 僅成員可讀。"""
    role = await project_role(session, project, identity.user)
    if role is not None or identity.user.is_admin:
        return role
    if project.visibility is Visibility.internal:
        return None
    raise problems.not_found(f"找不到專案 {project.slug}")  # 不洩漏 private 專案是否存在


async def require_project_role(
    session: AsyncSession, project: Project, identity: Identity, minimum: ProjectRole
) -> ProjectRole:
    role = await project_role(session, project, identity.user)
    if role is None or _ROLE_RANK[role] < _ROLE_RANK[minimum]:
        if identity.user.is_admin:
            return ProjectRole.owner
        raise problems.forbidden(f"需要專案 {minimum.value} 以上的權限。")
    return role


async def project_out(
    session: AsyncSession,
    project: Project,
    identity: Identity,
    settings: Settings,
    role: ProjectRole | None = None,
    role_known: bool = False,
) -> ProjectOut:
    """組出「當事人視角」的專案輸出。

    為什麼要有這個函式:`ProjectOut` 有兩個欄位**不是 ORM 欄位**——
    `my_role`(看的人是誰決定)與 `quota_bytes`(政策值,來自設定)。
    這段組裝原本散在 8 個地方,漏掉任何一處就是一個欄位靜默變 null 的 bug,
    而且不會有任何錯誤訊息。集中在這裡,只有一條路。

    參數:session、project、identity 當事人、settings、
    role/role_known 已知角色時可省一次查詢(role=None 且 role_known=True 表示「確定沒有角色」)。
    回傳:填好的 ProjectOut。副作用:可能查一次 project_members。
    """
    out = ProjectOut.model_validate(project)
    out.my_role = role if role_known else await project_role(session, project, identity.user)
    out.quota_bytes = project_limit(settings, project)
    return out


def parse_uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise problems.not_found(f"找不到{what}") from None
