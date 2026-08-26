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
    """版本狀態(T102 起三態)。

    T102(Benny 2026-08-26 裁示):**所有版本都要審**——作者只能把 draft 送審,
    published 只能由平台管理員核准產生。in_review 的可見性與 draft 同一待遇
    (非成員 404),「最新版」(F26)只認 published。
    既有已發布版本於 migration 0009 視為已核准,原樣保留。
    """

    draft = "draft"
    in_review = "in_review"  # 待審:作者已送出,等平台管理員核准/退回
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
    # T59(契約 §4.2a L1):顯示名稱快取——僅 `name` claim、僅自本人登入的 token、
    # 每次登入覆寫、**僅管理後台顯示**、不進 log、可整批清除(tools/purge_name_cache.py)。
    # 🔴 claim 可能不存在(name 由 firstName+lastName 推導,皆空則無)→ 本欄為 NULL,
    # 畫面 fallback 到 sub——這是會真的走到的路徑,不是防禦性假設。
    display_name_cache: Mapped[str | None] = mapped_column(String(255), default=None)
    # T102(契約 §4.2b):通知用信箱快取——僅自本人登入 token、僅 `email_verified=true`
    # 才落地、每次登入覆寫、只用於寄「待審版本」通知、不顯示於任何頁面、不進 log。
    # 🔴 **刻意無 unique、無 index**(§4.2b 第 9 條):email 不得作查詢鍵,
    # 留了索引這個口,快取會慢慢長成第二個使用者索引。清除:tools/purge_notify_email.py。
    notify_email_cache: Mapped[str | None] = mapped_column(String(255), default=None)
    # T102(契約 §4.2b 第 4/8 條):待審通知的訂閱開關,**預設關**——
    # 打開那一下就是條文要的「明示訂閱」,同一顆鈕就是退訂。只有平台管理員用得到,
    # 但放在 users 上而不是另立表:它就是「這個人要不要收信」一個布林,不值得一張表。
    review_email_opt_in: Mapped[bool] = mapped_column(default=False, nullable=False)
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
    # T102 起 `published_at` 的語意=**管理員核准的時刻**(不再是作者按發布的時刻);
    # F26「最新版」與歷史頁排序沿用本欄,語意換了但排序自動一致。
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # --- T102 審核欄位 ---
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # 核准/退回者。⚠ 0009 之前的 published 列此欄為 NULL=「上線前發布,當年沒人審過」
    # ——誠實留白,不偽造審核紀錄。
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, default=None
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # 退回理由(Benny 裁示:退回必填)。存在版本列上給作者看;
    # 🔴 不進稽核——AuditEvent 的 target_label 不收使用者自由文字。重送時清空。
    review_note: Mapped[str] = mapped_column(Text, default="", nullable=False)

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
    # 下載次數(F43)。算的是**發起**下載的次數(回應建構的那一刻),不是完成下載——
    # 中途中斷仍算一次。這個數字的用途是熱門度而非計費,不值得為了幾個中斷的請求
    # 把串流路徑複雜化。累計一律用原子 UPDATE,見 routers/artifacts.py 的說明。
    # 🔴 刻意**不做事件表**:F43 只要求次數,「誰下載了什麼」是稽核(T38/F54)的職責。
    #    用計數欄位的話,「統計不記個資」是結構上做不到,而不是靠自律。
    download_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    scan_status: Mapped[ScanStatus] = mapped_column(
        _enum(ScanStatus, "scan_status"), default=ScanStatus.not_scanned, nullable=False
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    release: Mapped[Release] = relationship(back_populates="artifacts")


class AuditEvent(Base):
    """稽核事件(F54 / T38):誰在何時做了什麼。

    🔴 **只存 `actor_id`**,不存 email / 姓名——與本檔開頭的紅線一致。
    要顯示是誰,查詢時再拿 `actor_id` 換 `sub`、向 IdP 取即時資料。
    稽核不是繞過個資紅線的後門。

    🔴 **`target_label` 是快照,不是外鍵。**
    稽核最重要的用途之一是「誰刪掉了什麼」,而東西一旦刪掉,`target_id` 就
    join 不回任何東西——結果是「某人在某時刪了 `9f2c…`」,最需要它的時候最沒用。
    所以寫入的當下就把人可讀的定位(slug / version / filename)快照下來。
    稽核記的是**當時的事實**,不是現在的狀態;這就是快照與外鍵的差別。

    ⚠️ `target_label` 只放識別用字串,**不放使用者輸入的自由文字**
    (專案摘要、版本說明)——那會讓稽核表變成個資的第二個落地處。

    副作用:本表只增不改;清除由保留期工具負責(見 `app/audit.py::purge_expired`)。
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        # 查詢一律「最近的在前」,再依 action / 目標篩選。
        Index("ix_audit_occurred_at", "occurred_at"),
        Index("ix_audit_action", "action"),
        Index("ix_audit_target", "target_type", "target_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    # 🔴 `action` 刻意用 String 而非 `_enum()`(其他欄位都用後者)。
    # 理由:稽核動作的字彙會隨功能持續成長,把它釘進 schema 等於「每加一個被稽核的
    # 動作就要一次 migration」——而**阻力會讓人選擇不記**,那是稽核最糟的失效方式:
    # 表存在、看起來正常,但漏掉的動作不會留下任何跡象。
    # 守門改由 `audit.AuditAction`(唯一字彙來源)加 test_audit.py 的兩條測試負責。
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    # 🔴 RESTRICT 而非 CASCADE:稽核紀錄不得因為使用者被刪除而消失。
    # 換句話說,有稽核紀錄的使用者就刪不掉——這是刻意的。
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # 目標可能已被刪除,所以**不是**外鍵——留著 UUID 供比對,查不回去也無妨。
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    target_label: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # 事件當下的請求識別碼,可與 stdout log 互相對照。
    trace_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)


class IssueStatus(enum.StrEnum):
    """問題回報的狀態(T77)。

    🔴 `resolved`(我方認為修好了)與 `closed`(結案)刻意分開:
    中間留一段讓回報者能說「還是不行」。若只有一個「已完成」,
    使用者就沒有位置表達異議,回報系統會慢慢變成單向的許願池。
    """

    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"
    closed = "closed"
    wontfix = "wontfix"


class Issue(Base):
    """使用者回報的網站問題(T77 / 設計文件《問題回報系統》)。

    🔴 內容存 **Markdown 原文**,不存轉譯後的 HTML——轉譯規則改了要能立即全站生效,
    而且資料庫裡永遠不該躺著一段「可執行的東西」。
    """

    __tablename__ = "issues"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # 🔴 RESTRICT:有回報紀錄的使用者刪不掉,與稽核同一個立場。
    reporter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[IssueStatus] = mapped_column(
        _enum(IssueStatus, "issue_status"), default=IssueStatus.open, nullable=False
    )
    # 使用者按下回報時所在的頁面,與當下的服務版本——兩者都是使用者不會記得、
    # 但排查時最需要的資訊,所以由系統自動帶入而不是要人填。
    page_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    app_version: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    closed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )

    reporter: Mapped["User"] = relationship(foreign_keys=[reporter_id])
    comments: Mapped[list["IssueComment"]] = relationship(
        back_populates="issue", cascade="all, delete-orphan", order_by="IssueComment.created_at"
    )
    attachments: Mapped[list["IssueAttachment"]] = relationship(
        back_populates="issue", cascade="all, delete-orphan", order_by="IssueAttachment.created_at"
    )

    __table_args__ = (Index("ix_issues_status_created", "status", "created_at"),)


class IssueComment(Base):
    """回報的討論串:回報者補充說明,或管理員回覆處理進度(T77)。"""

    __tablename__ = "issue_comments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # CASCADE:回報刪掉時討論串一起走,不留孤兒(與 users 的 RESTRICT 是不同考量)。
    issue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    # 是否為平台方回覆——顯示時要讓使用者一眼看出「這是官方回應」。
    is_staff_reply: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    issue: Mapped[Issue] = relationship(back_populates="comments")
    author: Mapped["User"] = relationship(foreign_keys=[author_id])


class IssueAttachment(Base):
    """回報的附件——**只收圖片**(T78)。

    🔴 `content_type` 存的是**判定出來的**型別,不是使用者宣稱的:
    inline 顯示時要用它當 `Content-Type`,若信任宣稱值,等於讓上傳者決定
    瀏覽器怎麼解讀那個位元組流,那正是 inline 路徑最危險的地方。
    """

    __tablename__ = "issue_attachments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    issue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    issue: Mapped["Issue"] = relationship(back_populates="attachments")
