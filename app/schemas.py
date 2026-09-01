"""API 輸入輸出結構。

平台規約:回應一律 JSON、時間 **ISO 8601 含時區**(pydantic 對 aware datetime 預設就是),
且**不得**輸出 email / 姓名以外的個資落地欄位——顯示用資料一律來自 IdP,不來自業務庫。
"""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import (
    ArtifactKind,
    PlatformRole,
    ProjectRole,
    QuotaTier,
    ReleaseStatus,
    ScanStatus,
    UploadStatus,
    UserStatus,
    Visibility,
)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- me ---------------------------------------------------------------------


class MeOut(BaseModel):
    user_id: uuid.UUID
    sub: str
    status: UserStatus
    platform_role: PlatformRole
    # 以下兩個由 IdP 即時提供,不存在業務庫。
    email: str | None = None
    name: str | None = None


# --- 專案 -------------------------------------------------------------------


class ProjectCreate(BaseModel):
    # T96:改為**選填** —— 網頁表單已不再詢問,由 `slugs.unique_slug()` 自名稱產生。
    # 🔴 API 仍可指定(向下相容:腳本使用者要的正是可預測的短名);
    #    指定時走的是與從前一字不差的同一條驗證。
    slug: str | None = Field(default=None, description="網址用短名;省略則由名稱自動產生")
    name: str = Field(min_length=1, max_length=128)
    summary: str = Field(default="", max_length=2000)
    visibility: Visibility = Visibility.internal

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, v: str | None) -> str | None:
        # T96:None 或空字串 = 「請自動產生」,交給呼叫端;有填就照舊嚴格驗。
        if v is None or not v.strip():
            return None
        v = v.strip().lower()
        if not SLUG_RE.match(v):
            raise ValueError("slug 需為 3–64 字元的小寫英數與連字號,且不以連字號開頭或結尾")
        return v


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    summary: str | None = Field(default=None, max_length=2000)
    visibility: Visibility | None = None


class ProjectOut(ORMModel):
    id: uuid.UUID
    slug: str
    name: str
    summary: str
    visibility: Visibility
    owner_id: uuid.UUID
    total_bytes: int
    created_at: datetime
    updated_at: datetime
    my_role: ProjectRole | None = None
    tags: list[str] = []
    quota_tier: QuotaTier = QuotaTier.standard
    # 級距對應的上限位元組數。不是 ORM 欄位(政策值來自設定),
    # 由 security.project_out() 統一填,前端才能顯示「已用 x / 上限 y」。
    quota_bytes: int | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def _flatten(cls, value):
        """ORM 給的是 ProjectTag 物件清單,對外只吐字串。"""
        if value and not isinstance(value[0], str):
            return sorted(item.tag for item in value)
        return sorted(value or [])


class MemberIn(BaseModel):
    user_id: uuid.UUID
    role: ProjectRole


class OwnerTransfer(BaseModel):
    """轉移擁有權的目標人選(F16)。"""

    user_id: uuid.UUID


class QuotaIn(BaseModel):
    """設定專案容量級距(F17)。只有平台管理員送得進來。"""

    tier: QuotaTier


MAX_TAGS_PER_PROJECT = 10
MAX_TAG_LENGTH = 32


def normalise_tag(raw: str) -> str:
    """把標籤正規化成可比對、可放進網址的形式(F42)。

    - 去前後空白、轉小寫:`Python` 與 `python` 必須是同一個標籤,否則篩選會漏掉一半
    - **不允許內含空白**:標籤要能直接放進查詢字串,`?tag=資料 分析` 只會製造麻煩
    - 允許中文——`工具`、`報表` 是實際會用的標籤

    參數:未正規化的標籤字串。回傳:正規化後的值。不合法時拋 ValueError(由 FastAPI 轉 422)。
    """
    tag = raw.strip().lower()
    if not tag:
        raise ValueError("標籤不可為空白")
    if any(ch.isspace() for ch in tag):
        raise ValueError(f"標籤不可含空白:{raw!r}(請改用連字號)")
    if len(tag) > MAX_TAG_LENGTH:
        raise ValueError(f"標籤長度不可超過 {MAX_TAG_LENGTH} 字元:{raw!r}")
    return tag


class TagsIn(BaseModel):
    """整組取代專案標籤(F42)。

    用「整組取代」而非逐個增刪:前端就是一個標籤輸入框,送出時本來就是整組;
    整組取代天然冪等,也不必處理「加一個已存在的標籤」這種邊角。
    """

    tags: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def _normalise(cls, raw: list[str]) -> list[str]:
        seen: list[str] = []
        for item in raw:
            tag = normalise_tag(item)
            if tag not in seen:  # 同一次請求內自動去重
                seen.append(tag)
        if len(seen) > MAX_TAGS_PER_PROJECT:
            raise ValueError(f"每個專案最多 {MAX_TAGS_PER_PROJECT} 個標籤,收到 {len(seen)} 個")
        return sorted(seen)


class TagCount(BaseModel):
    tag: str
    project_count: int


class TagPage(BaseModel):
    items: list[TagCount]


class MemberOut(BaseModel):
    user_id: uuid.UUID
    sub: str
    role: ProjectRole
    created_at: datetime


# --- 版本 -------------------------------------------------------------------


class ReleaseCreate(BaseModel):
    version: str
    notes: str = Field(default="", max_length=20000)

    @field_validator("version")
    @classmethod
    def _check_version(cls, v: str) -> str:
        v = v.strip()
        if not VERSION_RE.match(v):
            raise ValueError("version 只能包含英數與 . _ + -,長度 1–64")
        return v


class ReleaseUpdate(BaseModel):
    notes: str | None = Field(default=None, max_length=20000)


class ArtifactOut(ORMModel):
    id: uuid.UUID
    release_id: uuid.UUID
    kind: ArtifactKind
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    upload_status: UploadStatus
    scan_status: ScanStatus
    download_count: int = 0
    created_at: datetime
    completed_at: datetime | None


class ProjectCommentCreate(BaseModel):
    """留一則專案回饋(T124)。長度上限比照問題回報的討論串。"""

    body_markdown: str = Field(min_length=1, max_length=5000)

    @field_validator("body_markdown")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("留言不可空白")
        return value


class ProjectCommentOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    body_markdown: str
    created_at: datetime
    # 🔴 只帶 `author_id`(資料庫 id),**不帶名字也不帶 sub**:
    # 名字受契約 §4.2a L1 限制;sub 是 IdP 的識別碼,沒有理由讓 API 整批吐出來。
    # 畫面上的「誰留的」由網頁層另外批次組(見 web.py 的 `_labels_by_id`)。
    author_id: uuid.UUID


class ReleaseReject(BaseModel):
    """退回一個待審版本時要附的理由(T123)。

    🔴 **必填,且不接受純空白**。沒有理由的退回,作者只能猜,然後重傳一次一模一樣
    的東西——理由欄位不是裝飾,是這個流程能不能運轉的關鍵。
    `min_length=1` 擋不掉 `"   "`,所以另外 strip 後再驗一次。
    """

    note: str = Field(min_length=1, max_length=2000)

    @field_validator("note")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("退回理由不可空白")
        return value


class ReleaseOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    version: str
    notes: str
    status: ReleaseStatus
    created_by_id: uuid.UUID
    created_at: datetime
    published_at: datetime | None
    # T123 審核結果。🔴 只帶「退回理由」與時間,**不帶審核者的任何身分資訊**——
    # `reviewed_by_id` 刻意不出現在 API:誰審的屬於稽核紀錄的範疇,
    # 而稽核不是繞過個資紅線的後門(AuditEventOut 的 docstring 早已寫明同一件事)。
    review_note: str = ""
    reviewed_at: datetime | None = None
    artifacts: list[ArtifactOut] = []
    # 版本的下載次數是底下所有檔案的加總,**不另存欄位**(F43):
    # 兩個計數器分開存,刪檔或補傳漏掉一次就永遠對不起來。
    # artifacts 本來就 selectin 一起載入,加總不用額外查詢。
    download_count: int = 0

    @model_validator(mode="after")
    def _sum_downloads(self) -> "ReleaseOut":
        self.download_count = sum(a.download_count for a in self.artifacts)
        return self


# --- 管理 -------------------------------------------------------------------


class UserOut(ORMModel):
    id: uuid.UUID
    sub: str
    status: UserStatus
    platform_role: PlatformRole
    created_at: datetime
    activated_at: datetime | None
    last_login_at: datetime | None


class UserPatch(BaseModel):
    status: UserStatus | None = None
    platform_role: PlatformRole | None = None


# --- 共用 -------------------------------------------------------------------


class Page(BaseModel):
    total: int
    limit: int
    offset: int


class ProjectPage(Page):
    items: list[ProjectOut]


class ReleasePage(Page):
    items: list[ReleaseOut]


class UserPage(Page):
    items: list[UserOut]


class AuditEventOut(ORMModel):
    """一筆稽核事件(F54)。

    🔴 只有 `actor_id`,沒有 email / 姓名——要顯示是誰,呼叫端拿 `actor_id`
    換 `sub` 後自行向 IdP 取即時資料。稽核不是繞過個資紅線的後門。
    """

    id: uuid.UUID
    occurred_at: datetime
    action: str
    actor_id: uuid.UUID | None
    target_type: str
    target_id: uuid.UUID | None
    # 事發當下的人可讀快照(slug / version / filename);目標刪除後靠它才知道刪了什麼。
    target_label: str
    trace_id: str


class AuditPage(Page):
    items: list[AuditEventOut]
