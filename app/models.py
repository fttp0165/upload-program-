"""資料模型。

🔴 平台紅線:業務庫只存 `sub`(對應 JWT 的使用者識別),**不存** email / 姓名 / 密碼。
顯示用的 email 與名稱一律由 IdP 的 ID token / userinfo 即時提供,不落地。
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class UserStatus(enum.StrEnum):
    pending = "pending"  # 首登建立,零角色 —— 業務 API 一律 403 待開通
    active = "active"
    disabled = "disabled"


class PlatformRole(enum.StrEnum):
    member = "member"
    admin = "admin"  # 可開通使用者、指派平台角色


class Visibility(enum.StrEnum):
    internal = "internal"  # 所有已開通使用者可讀
    private = "private"  # 僅專案成員可讀


class QuotaTier(enum.StrEnum):
    """專案容量級距(F17)。

    專案列上只存**級距代號**,不存位元組數字:政策數字散進每一列的話,
    日後把標準級距從 2 GB 調成 3 GB 就變成一次資料遷移,
    而且分不清哪些列是政策預設、哪些是個案調整。代號→位元組的對應見 `app/quota.py`。
    """

    standard = "standard"  # 預設,2 GB
    extended = "extended"  # 需向平台管理員申請,10 GB


class ProjectRole(enum.StrEnum):
    owner = "owner"
    maintainer = "maintainer"
    viewer = "viewer"


class ReleaseStatus(enum.StrEnum):
    draft = "draft"
    published = "published"


class ArtifactKind(enum.StrEnum):
    source = "source"  # 原始碼(壓縮檔)
    binary = "binary"  # 執行檔 / 安裝包
    doc = "doc"  # 程式文件


class UploadStatus(enum.StrEnum):
    pending = "pending"  # 已登記,等前端直傳完成
    ready = "ready"  # 已完成並通過驗證
    failed = "failed"


class ScanStatus(enum.StrEnum):
    not_scanned = "not_scanned"  # ⏳ MVP 未接掃毒;下載頁必須據此標示
    clean = "clean"
    infected = "infected"


def _enum(py_enum: type[enum.Enum], name: str) -> Enum:
    # native_enum=False → 存成 VARCHAR + CHECK,PostgreSQL 與 SQLite(測試)都能跑,
    # 且日後加值不必動 PostgreSQL 的 ENUM 型別。
    return Enum(py_enum, name=name, native_enum=False, values_callable=lambda e: [m.value for m in e])


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # 🔴 唯一身分鍵 = JWT 的 sub;不可變、不可重複。這是本表唯一的身分欄位。
    sub: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    status: Mapped[UserStatus] = mapped_column(
        _enum(UserStatus, "user_status"), default=UserStatus.pending, nullable=False
    )
    platform_role: Mapped[PlatformRole] = mapped_column(
        _enum(PlatformRole, "platform_role"), default=PlatformRole.member, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    memberships: Mapped[list["ProjectMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_active(self) -> bool:
        return self.status is UserStatus.active

    @property
    def is_admin(self) -> bool:
        return self.is_active and self.platform_role is PlatformRole.admin


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    visibility: Mapped[Visibility] = mapped_column(
        _enum(Visibility, "visibility"), default=Visibility.internal, nullable=False
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    # 配額用的累計值,artifact 轉 ready / 被刪除時維護。
    total_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # 容量級距(F17);只有平台管理員能改,對應的上限位元組數見 app/quota.py。
    quota_tier: Mapped[QuotaTier] = mapped_column(
        _enum(QuotaTier, "quota_tier"), default=QuotaTier.standard, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    members: Mapped[list["ProjectMember"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    releases: Mapped[list["Release"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    # 專案幾乎總是連同標籤一起呈現(列表、詳情、篩選),用 selectin 一次查完;
    # 同時避免 T50 那類「序列化時才 lazy load」在 async session 下爆掉的問題。
    tags: Mapped[list["ProjectTag"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )


class ProjectTag(Base):
    """專案標籤(F42)。

    刻意**不做 tags / project_tags 兩張表的正規化**:MVP 沒有「重新命名標籤」的需求,
    而正規化會帶來孤兒標籤(最後一個專案移除後,`tags` 留下無人使用的列)這個實打實的
    維運負擔。列出所有標籤用 `SELECT DISTINCT tag` 就夠。
    日後真需要正規化,再做一次可回滾的 migration。

    `tag` 一律存**正規化後**的值(小寫、去空白),由 schema 層保證。
    """

    __tablename__ = "project_tags"
    __table_args__ = (UniqueConstraint("project_id", "tag", name="uq_project_tag"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tag: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped["Project"] = relationship(back_populates="tags")


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[ProjectRole] = mapped_column(_enum(ProjectRole, "project_role"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class Release(Base):
    __tablename__ = "releases"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_release_version"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[ReleaseStatus] = mapped_column(
        _enum(ReleaseStatus, "release_status"), default=ReleaseStatus.draft, nullable=False
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    project: Mapped[Project] = relationship(back_populates="releases")
    # 🐛 根本原因(T50):原本是預設的 lazy="select",序列化 ReleaseOut 時才去碰 artifacts,
    # 在 async session 下會觸發同步 lazy load 而拋 MissingGreenlet。
    # 版本幾乎總是連同檔案一起呈現,直接設為 selectin 一次查完,也讓這類漏載入不再可能發生。
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="release", cascade="all, delete-orphan", lazy="selectin"
    )


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("release_id", "filename", name="uq_artifact_filename"),
        Index("ix_artifact_sha256", "sha256"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("releases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[ArtifactKind] = mapped_column(_enum(ArtifactKind, "artifact_kind"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # 伺服器判定的 MIME(依 magic bytes),不是前端宣稱的值。
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    hash_verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    upload_status: Mapped[UploadStatus] = mapped_column(
        _enum(UploadStatus, "upload_status"), default=UploadStatus.pending, nullable=False
    )
    scan_status: Mapped[ScanStatus] = mapped_column(
        _enum(ScanStatus, "scan_status"), default=ScanStatus.not_scanned, nullable=False
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    release: Mapped[Release] = relationship(back_populates="artifacts")
