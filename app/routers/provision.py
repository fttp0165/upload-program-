"""開通即建帳(SVC-PUSH)接收端點 —— T146 建立、T147 改為 B 案語意。

## 這支在做什麼

portal-admin 的管理員在後台按「+ upload」把某人加進 `/svc/upload` 之後,
portal 會**主動**呼叫這裡:「這個 `sub` 被開通了」—— 本服務**直接開通**他;
portal 取消時送 `leave`,本服務**直接停用**他。本服務管理員不必再按第二次。
(T146 原為「先建一列 pending,等本服務管理員開通」,T147 依 Benny 裁示改掉。)

依據:cats-portal 徵詢函 v1.0(2026-09-09 18:00 UTC+8),
原樣存於 `docs/plans/inbox/cats-portal_致_upload-program_徵詢_開通即建帳推送端點_20260909-1800.md`。

## 🔴 三件刻意的事,各自擋什麼

1. **portal 的開關就是本服務的開關**(T147,Benny 2026-10-07 裁示 B 案)。
   `join` → active、`leave` → disabled,兩者必須一起成立 —— 只做前者會「開得了、關不掉」。
   ⚠ 取捨:本服務管理員手動停用的人,portal 再按開通就會恢復(沒有欄位區分誰停用的)。
   ⚠ T63「`/svc/upload` 只讀不判」不受影響:那條管**登入 token 的 groups**,這裡是推送。
2. **不重用 `security.upsert_user()`。** 它的語意是「每次登入以 token claims 覆寫
   名字與信箱,含覆寫成 NULL」;推送沒有 claims,重用的話**每推一次就把那個人的
   名字抹掉一次**,而畫面只會悄悄退回顯示 UUID。
3. **不走 `get_identity()`。** 那條會替呼叫者首登自建 —— 每個按過按鈕的 portal
   管理員都會憑空變成本服務的 pending 使用者。這裡只**驗** token,不建呼叫者。

## 驗證四層(徵詢函 §2.1),deny-by-default

    ① 簽章 / RS256 / iss / exp  → 401(沿用 OidcClient.verify_access_token)
    ② aud **明確**含本服務     → 401(不吃 azp 退路,見 `_has_audience`)
    ③ scope 含本服務的 provision scope → 403
    ④ 呼叫者本人是平台管理員或部門主管 → 403

③ 說「這張票允許走這條路」,④ 說「這個人有資格」——**兩者缺一不可**。
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .. import problems
from ..audit import AuditAction, record
from ..config import Settings
from ..models import PlatformRole, User, UserStatus
from ..oidc import OidcClient
from ..security import DbSession, _bearer_token

router = APIRouter(prefix="/v1", tags=["provision"])
log = logging.getLogger(__name__)


class ProvisionIn(BaseModel):
    """推送 payload。**就這三個欄位。**

    🔴 `extra="forbid"`:多一個 `email` / `name` 就 422,而不是默默吃下。
    契約紅線「業務庫只存 sub」在 portal 那側由 AST 檢查守著;這裡是我方這一側的
    結構保證 —— 兩邊各守一道,任何一邊漂移都會當場紅,而不是個資悄悄落地。
    """

    model_config = ConfigDict(extra="forbid")

    subject: str
    service: str
    action: Literal["join", "leave"]

    @field_validator("subject")
    @classmethod
    def _canonical_uuid(cls, v: str) -> str:
        """Keycloak 的 sub 是**小寫標準形**的 UUID。

        🔴 要求「完全等於標準形」而不只是「解析得出 UUID」:大寫或去掉連字號的
        同一個 UUID 解析得出來,但存進去之後與本人登入時的 `sub` **字串不相等** ——
        結果是一列永遠不會被任何人登入的幽靈,外加本人首登時再建一列。
        """
        try:
            canonical = str(uuid.UUID(v))
        except (ValueError, AttributeError, TypeError):
            raise ValueError("subject 必須是 UUID") from None
        if canonical != v:
            raise ValueError("subject 必須是小寫標準形 UUID")
        return v


class ProvisionOut(BaseModel):
    """回應。portal 只看狀態碼不解讀主體(徵詢函 §2.4),主體是給人除錯用的。

    result:
      created   —— 新建一列並開通(201)
      activated —— 既有列 pending/disabled → active
      disabled  —— 既有列 active/pending → disabled
      unchanged —— 已經是目標狀態(重送)
      ignored   —— leave 一個本服務從沒見過的人
    """

    subject: str
    result: Literal["created", "activated", "disabled", "unchanged", "ignored"]


def _has_audience(claims: dict, client_id: str) -> bool:
    """`aud` 是否**明確**含本服務 client_id(字串或陣列皆可)。

    🔴 為什麼不信 `verify_access_token` 就好:它有一條 **azp 退路**(Keycloak 的登入
    access token 有時 aud 只放 `account`,azp 才是 client_id)。那條退路是給**本服務
    自己的登入 token** 用的;推送 token 的 azp 是 `portal-admin`,它能進來的唯一理由
    是 portal 的 audience mapper 把本服務寫進了 aud(契約 §11.3)。這裡把那件事驗明。
    """
    aud = claims.get("aud")
    if isinstance(aud, str):
        return aud == client_id
    if isinstance(aud, list):
        return client_id in aud
    return False


def _has_scope(claims: dict, wanted: str) -> bool:
    """`scope` claim(空白分隔字串)是否含指定 scope。比對整個詞,不做子字串比對。"""
    scope = claims.get("scope")
    return isinstance(scope, str) and wanted in scope.split()


def _is_privileged(claims: dict, settings: Settings) -> bool:
    """呼叫者本人是不是平台管理員或部門主管(徵詢函 §2.1 第 3 條)。

    🔴 一律讀**當次 token** 的 claims,不查任何本地清單:
    portal 那邊被拔掉管理員 / 調離主管,下一張 token 就不再帶,這裡立刻失效。
    🔴 主管判準是**前綴** `/mgr/`(含結尾斜線),不是子字串 ——
    `/org/mgr-team` 這種名字不得讓人變成主管。

    ⚠ 本服務**驗不到管轄範圍**(那需要 portal 的組織資料):主管能不能開通「這個人」
    由 portal 的 `_guard_target` 把關。這裡擋的是「根本不是管理者的人拿到 scope」。
    """
    roles = (claims.get("realm_access") or {}).get("roles") or []
    if settings.provision_admin_role in roles:
        return True
    groups = claims.get("groups") or []
    prefix = settings.provision_manager_group_prefix
    return isinstance(groups, list) and any(
        isinstance(g, str) and g.startswith(prefix) for g in groups
    )


def verify_provision_caller(request: Request) -> dict:
    """四層驗證,通過則回傳呼叫者 claims。

    參數:request(取 Authorization header 與 app.state)。
    回傳:呼叫者的 token claims。
    副作用:無 —— 刻意**不**建呼叫者的本地帳號(見檔頭第 3 點)。
    例外:401(①②)/ 403(③④),一律 RFC 7807。
    """
    token = _bearer_token(request)
    if not token:
        raise problems.unauthorized("缺少憑證:請帶 Authorization: Bearer。")

    settings: Settings = request.app.state.settings
    oidc: OidcClient = request.app.state.oidc
    claims = oidc.verify_access_token(token)  # ① 失敗即拋 401

    if not _has_audience(claims, settings.oidc_client_id):  # ②
        raise problems.unauthorized("token 的 aud 不含本服務")
    if not _has_scope(claims, settings.provision_scope):  # ③
        raise problems.forbidden(f"token 缺少 scope {settings.provision_scope}")
    if not _is_privileged(claims, settings):  # ④
        raise problems.forbidden("需要平台管理員或部門主管身分才能推送建帳。")
    return claims


async def _is_last_active_admin(session, user: User) -> bool:
    """`user` 是不是本服務**唯一一位** active 平台管理員。

    🔴 與 `admin.patch_user`「不能停用自己」同一個理由:平台可能一個管理員都不剩,
    而那時連「把人開通回來」的後台都進不去,只剩手打 SQL。
    """
    if not (user.is_active and user.platform_role is PlatformRole.admin):
        return False
    others = (
        await session.execute(
            select(func.count())
            .select_from(User)
            .where(
                User.id != user.id,
                User.status == UserStatus.active,
                User.platform_role == PlatformRole.admin,
            )
        )
    ).scalar_one()
    return others == 0


@router.post(
    "/provision",
    response_model=ProvisionOut,
    summary="portal 開通/取消 = 本服務開通/停用(SVC-PUSH 接收端)",
    status_code=status.HTTP_200_OK,
)
async def provision(
    payload: ProvisionIn,
    request: Request,
    response: Response,
    session: DbSession,
    caller: Annotated[dict, Depends(verify_provision_caller)],
) -> ProvisionOut:
    """依 `action` 把本地 `users.status` 對齊 portal 的開關(T147,B 案)。

    - `join`:沒有 → 建一列 **active**(201 `created`);pending/disabled → active;active → 不動。
    - `leave`:active/pending → **disabled**;disabled → 不動;沒有 → 不建列(`ignored`)。
      🔴 唯一一位 active 平台管理員 → **409**,不動(見 `_is_last_active_admin`)。

    🔴 **只動 `status`(與 `activated_at`)**:名字、信箱、`platform_role` 一個都不碰 ——
       推送管的是「能不能用」,不是「是誰」也不是「是不是本服務管理員」。

    冪等:以 `users.sub` 唯一鍵;已是目標狀態時回 `unchanged`、不寫稽核。
    副作用:可能 INSERT / UPDATE 一列 `users`,並寫 `audit_events`(同一次 commit)。
    """
    settings: Settings = request.app.state.settings
    if payload.service != settings.provision_service:
        # 拿 survey 的推送打到這裡 = portal 的 URL 設錯了。配置錯誤要大聲,不要動資料。
        raise problems.unprocessable(
            "provision-wrong-service",
            "服務代號不符",
            f"本端點只接受 service={settings.provision_service}",
        )

    # actor:呼叫者若剛好也是本服務使用者就記他的本地 id;不是就記 None(系統來源)。
    # 🔴 **不為此建呼叫者的帳號** —— 見檔頭第 3 點。完整的「誰按的」在 portal 的稽核裡,
    #    兩邊以 X-Trace-Id 對帳(TraceMiddleware 已把它帶進本請求的每一行 log)。
    actor_id = (
        await session.execute(select(User.id).where(User.sub == caller.get("sub")))
    ).scalar_one_or_none()
    user = (
        await session.execute(select(User).where(User.sub == payload.subject))
    ).scalar_one_or_none()

    if payload.action == "leave":
        if user is None:
            return ProvisionOut(subject=payload.subject, result="ignored")
        if user.status is UserStatus.disabled:
            return ProvisionOut(subject=payload.subject, result="unchanged")
        if await _is_last_active_admin(session, user):
            raise problems.conflict(
                "對象是本服務唯一一位平台管理員,不能由 portal 取消開通來停用;"
                "請先在本服務後台指派另一位管理員。"
            )
        user.status = UserStatus.disabled
        # action 由**新狀態**決定,與後台按鈕同一個字彙(test_audit.py 的既有原則)。
        record(session, action=AuditAction.user_disable, actor_id=actor_id,
               target_type="user", target_id=user.id)
        await session.commit()
        return ProvisionOut(subject=payload.subject, result="disabled")

    # ── join ──
    if user is not None:
        if user.is_active:
            return ProvisionOut(subject=payload.subject, result="unchanged")
        user.status = UserStatus.active
        if user.activated_at is None:
            user.activated_at = datetime.now(UTC)
        record(session, action=AuditAction.user_activate, actor_id=actor_id,
               target_type="user", target_id=user.id)
        await session.commit()
        return ProvisionOut(subject=payload.subject, result="activated")

    user = User(
        sub=payload.subject,
        status=UserStatus.active,
        platform_role=PlatformRole.member,
        activated_at=datetime.now(UTC),
        # 名字與信箱刻意留空:推送不帶個資,本人登入時由 upsert_user 依 §4.2a 填入。
    )
    session.add(user)
    await session.flush()  # 取得 user.id 給稽核用
    # 兩筆:「建了這一列」與「開通了這個人」是兩件事;後者與後台按鈕同一個字彙,
    # 查「誰被開通了」時不必知道是哪條路開的。
    record(session, action=AuditAction.user_provision, actor_id=actor_id,
           target_type="user", target_id=user.id)
    record(session, action=AuditAction.user_activate, actor_id=actor_id,
           target_type="user", target_id=user.id)
    try:
        await session.commit()
    except IntegrityError:
        # 並發:同一個人剛好在這一刻首登(建成 pending)、或 portal 重試撞上第一次。
        # 不猜對方建成什麼狀態 —— 回 409 讓 portal 顯示失敗,管理員重按一次即收斂。
        await session.rollback()
        raise problems.conflict("同一個帳號正在被同時建立,請重試一次。") from None

    response.status_code = status.HTTP_201_CREATED
    return ProvisionOut(subject=payload.subject, result="created")
