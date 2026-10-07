"""開通即建帳(SVC-PUSH)接收端點 —— T146。

## 這支在做什麼

portal-admin 的管理員在後台按「+ upload」把某人加進 `/svc/upload` 之後,
portal 會**主動**呼叫這裡:「這個 `sub` 被開通了,請先開一列」。
讓本服務的管理員**不必等那個人第一次登入**,就能在後台開通他、設角色、設上限。

依據:cats-portal 徵詢函 v1.0(2026-09-09 18:00 UTC+8),
原樣存於 `docs/plans/inbox/cats-portal_致_upload-program_徵詢_開通即建帳推送端點_20260909-1800.md`。

## 🔴 三件刻意的事,各自擋什麼

1. **建的是 pending,不是 active。** 本服務的授權看本地 `status`,開通是本服務
   管理員的職權(契約 §4.4;T63「`/svc/upload` 只讀不判」)。推送若直接開通,
   portal 端任何能開群組的人就繞過了本服務的開通流程 —— 而那正是 T63 擋住的事。
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
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
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
    """回應。portal 只看狀態碼不解讀主體(徵詢函 §2.4),主體是給人除錯用的。"""

    subject: str
    result: Literal["created", "exists", "ignored"]


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


@router.post(
    "/provision",
    response_model=ProvisionOut,
    summary="開通即建帳(portal SVC-PUSH 接收端)",
    status_code=status.HTTP_200_OK,
)
async def provision(
    payload: ProvisionIn,
    request: Request,
    response: Response,
    session: DbSession,
    caller: Annotated[dict, Depends(verify_provision_caller)],
) -> ProvisionOut:
    """依 `action` 處理一筆推送。

    - `join`:沒有 → 建 pending 列(201 `created`,寫稽核);已有 → 一個欄位都不動(200 `exists`)。
    - `leave`:**忽略**(200 `ignored`),採徵詢函 §2.3 的平台預設。
      ⚠ 在本服務的實際效果:portal 取消開通**不會**讓那個人在這裡被停用 ——
      本服務的授權看本地 `status`,收權仍要本服務管理員動手(與推送上線前相同)。

    冪等:以 `users.sub` 唯一鍵 upsert;重送同一筆結果相同(徵詢函 §2.2)。
    副作用:可能新增一列 `users` 與一列 `audit_events`(同一次 commit)。
    """
    settings: Settings = request.app.state.settings
    if payload.service != settings.provision_service:
        # 拿 survey 的推送打到這裡 = portal 的 URL 設錯了。配置錯誤要大聲,不要建列。
        raise problems.unprocessable(
            "provision-wrong-service",
            "服務代號不符",
            f"本端點只接受 service={settings.provision_service}",
        )

    if payload.action == "leave":
        log.info("建帳推送:leave 依約忽略", extra={"caller_sub": caller.get("sub")})
        return ProvisionOut(subject=payload.subject, result="ignored")

    existing = (
        await session.execute(select(User.id).where(User.sub == payload.subject))
    ).scalar_one_or_none()
    if existing is not None:
        return ProvisionOut(subject=payload.subject, result="exists")

    user = User(
        sub=payload.subject,
        status=UserStatus.pending,
        platform_role=PlatformRole.member,
        # 名字與信箱刻意留空:推送不帶個資,本人登入時由 upsert_user 依 §4.2a 填入。
    )
    session.add(user)
    await session.flush()  # 取得 user.id 給稽核用

    # actor:呼叫者若剛好也是本服務使用者就記他的本地 id;不是就記 None(系統來源)。
    # 🔴 **不為此建呼叫者的帳號** —— 見檔頭第 3 點。完整的「誰按的」在 portal 的稽核裡,
    #    兩邊以 X-Trace-Id 對帳(TraceMiddleware 已把它帶進本請求的每一行 log)。
    actor_id = (
        await session.execute(select(User.id).where(User.sub == caller.get("sub")))
    ).scalar_one_or_none()
    record(
        session,
        action=AuditAction.user_provision,
        actor_id=actor_id,
        target_type="user",
        target_id=user.id,
    )
    try:
        await session.commit()
    except IntegrityError:
        # 並發:同一個人剛好在這一刻首登、或 portal 重試撞上第一次。結果等同「已存在」。
        await session.rollback()
        return ProvisionOut(subject=payload.subject, result="exists")

    response.status_code = status.HTTP_201_CREATED
    return ProvisionOut(subject=payload.subject, result="created")
