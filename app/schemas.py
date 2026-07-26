"""API 輸入輸出結構。

平台規約:回應一律 JSON、時間 **ISO 8601 含時區**(pydantic 對 aware datetime 預設就是),
且**不得**輸出 email / 姓名以外的個資落地欄位——顯示用資料一律來自 IdP,不來自業務庫。
"""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import (
    ArtifactKind,
    PlatformRole,
    ProjectRole,
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
    slug: str = Field(description="網址用短名,小寫英數與連字號")
    name: str = Field(min_length=1, max_length=128)
    summary: str = Field(default="", max_length=2000)
    visibility: Visibility = Visibility.internal

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, v: str) -> str:
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


class MemberIn(BaseModel):
    user_id: uuid.UUID
    role: ProjectRole


class OwnerTransfer(BaseModel):
    """轉移擁有權的目標人選(F16)。"""

    user_id: uuid.UUID


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
    created_at: datetime
    completed_at: datetime | None


class ReleaseOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    version: str
    notes: str
    status: ReleaseStatus
    created_by_id: uuid.UUID
    created_at: datetime
    published_at: datetime | None
    artifacts: list[ArtifactOut] = []


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
