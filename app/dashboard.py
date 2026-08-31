"""管理後台總覽的聚合查詢(T70;設計文件《管理員後台與數據面板》§4)。

**路由不寫 SQL**:所有數字由本模組的純函式算出,吃 `session` 與 `settings`、
回傳資料類別。這樣測試可以直接對函式斷言,T71 的趨勢與排行也擴充同一個地方,
不會出現「網頁算一套、API 算另一套」的分岔。

🔴 設計文件 §2 的紅線在這裡具體化:
1. **只做聚合,不按人拆解。** 下載統計一律是總數;`audit_events` 雖然存了
   `actor_id`(F54 稽核的職責),但面板不碰它——「資料剛好存在」不等於可以展示。
2. **查詢數固定**、清單一律 top-N,不隨資料量成長。
3. **算不出來的就不算**:例如每日登入數(`last_login_at` 只存最後一次),
   面板寧可說沒有,也不用近似值假裝有。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import (
    Artifact,
    Issue,
    IssueStatus,
    Project,
    QuotaTier,
    Release,
    ReleaseStatus,
    ScanStatus,
    UploadStatus,
    User,
    UserStatus,
    Visibility,
)
from .quota import limit_for

# --- 門檻(Benny 2026-07-31 確認採預設值)------------------------------------
# 放這裡而不是設定檔:它們是**治理判準**不是部署參數,改動應該經過程式碼審查。
STALE_DRAFT_DAYS = 14
QUOTA_WARN_RATIO = 0.8
TOP_N = 5


@dataclass(frozen=True)
class Kpis:
    """首排六張卡的數字。全部來自 COUNT / SUM,無明細。"""

    users_total: int = 0
    users_pending: int = 0
    projects_total: int = 0
    projects_private: int = 0
    releases_published: int = 0
    releases_draft: int = 0
    artifacts_total: int = 0
    artifacts_not_scanned: int = 0
    storage_used_bytes: int = 0
    storage_quota_bytes: int = 0
    downloads_total: int = 0


@dataclass(frozen=True)
class ProjectUsage:
    """逼近配額的專案(顯示用,不含任何個資)。"""

    slug: str
    name: str
    used_bytes: int
    limit_bytes: int
    tier: str

    @property
    def percent(self) -> int:
        """已用百分比(整數,四捨五入向下)。上限為 0 時回 0,避免除以零。"""
        return int(self.used_bytes * 100 / self.limit_bytes) if self.limit_bytes else 0


@dataclass(frozen=True)
class StaleDraft:
    """停滯的 draft:建立超過 STALE_DRAFT_DAYS 天仍未發布。"""

    project_slug: str
    project_name: str
    version: str
    days: int


@dataclass(frozen=True)
class OrphanProject:
    """擁有者已停用的專案——F16 轉移擁有權的觸發點。

    人離職、帳號停用之後,專案就沒有能改設定的人了;F16 早就做好,
    但在此之前**沒有任何地方會告訴管理員「該轉移了」**。
    """

    slug: str
    name: str
    owner_sub: str


@dataclass(frozen=True)
class Todos:
    """需要管理員動作的清單。空的代表沒事——這時頁面顯示「沒有待辦」而不是空表格。"""

    pending_users: int = 0
    not_scanned_artifacts: int = 0
    # T79:未處理的問題回報。本平台沒有 email 也沒有排程器,
    # 這個數字是管理員唯一會被提醒的地方——沒有它,回報會安靜地躺在資料庫裡。
    open_issues: int = 0
    # T123:待審版本。與 open_issues 同一個理由——平台沒有 email 也沒有推播,
    # 🔴 這個數字是管理員**唯一**會知道「有東西在等我審」的地方。
    # 少了它,作者送審之後版本就安靜地卡在資料庫裡,而作者會以為系統壞了。
    pending_reviews: int = 0
    quota_warnings: list[ProjectUsage] = field(default_factory=list)
    stale_drafts: list[StaleDraft] = field(default_factory=list)
    orphan_projects: list[OrphanProject] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            self.pending_users
            or self.open_issues
            or self.pending_reviews
            or self.not_scanned_artifacts
            or self.quota_warnings
            or self.stale_drafts
            or self.orphan_projects
        )


async def _scalar(session: AsyncSession, stmt: Select) -> int:
    """跑一個聚合查詢並保證回傳 int(SUM 在空表回 NULL)。"""
    return int((await session.execute(stmt)).scalar() or 0)


async def collect_kpis(session: AsyncSession, settings: Settings) -> Kpis:
    """算出六張 KPI 卡的數字。副作用:無(唯讀)。"""
    users_total = await _scalar(session, select(func.count()).select_from(User))
    users_pending = await _scalar(
        session,
        select(func.count()).select_from(User).where(User.status == UserStatus.pending),
    )
    projects_total = await _scalar(session, select(func.count()).select_from(Project))
    projects_private = await _scalar(
        session,
        select(func.count()).select_from(Project).where(Project.visibility == Visibility.private),
    )
    releases_published = await _scalar(
        session,
        select(func.count()).select_from(Release).where(Release.status == ReleaseStatus.published),
    )
    releases_draft = await _scalar(
        session,
        select(func.count()).select_from(Release).where(Release.status == ReleaseStatus.draft),
    )
    # 只計上傳完成的檔案:半途中斷的紀錄不是「平台上有這個檔案」。
    ready = Artifact.upload_status == UploadStatus.ready
    artifacts_total = await _scalar(session, select(func.count()).select_from(Artifact).where(ready))
    artifacts_not_scanned = await _scalar(
        session,
        select(func.count())
        .select_from(Artifact)
        .where(ready, Artifact.scan_status == ScanStatus.not_scanned),
    )
    storage_used = await _scalar(session, select(func.sum(Project.total_bytes)))
    downloads_total = await _scalar(session, select(func.sum(Artifact.download_count)))

    # 配額總和要按各專案的級距加總——不能用「專案數 × 標準級距」草率帶過,
    # 擴充級距(F17)的專案會讓那個算法失真。
    tier_counts = (
        await session.execute(
            select(Project.quota_tier, func.count()).group_by(Project.quota_tier)
        )
    ).all()
    storage_quota = sum(limit_for(settings, QuotaTier(tier)) * count for tier, count in tier_counts)

    return Kpis(
        users_total=users_total,
        users_pending=users_pending,
        projects_total=projects_total,
        projects_private=projects_private,
        releases_published=releases_published,
        releases_draft=releases_draft,
        artifacts_total=artifacts_total,
        artifacts_not_scanned=artifacts_not_scanned,
        storage_used_bytes=storage_used,
        storage_quota_bytes=storage_quota,
        downloads_total=downloads_total,
    )


async def collect_todos(session: AsyncSession, settings: Settings) -> Todos:
    """算出需要管理員動作的項目。副作用:無(唯讀)。"""
    kpi_pending = await _scalar(
        session,
        select(func.count()).select_from(User).where(User.status == UserStatus.pending),
    )
    not_scanned = await _scalar(
        session,
        select(func.count())
        .select_from(Artifact)
        .where(
            Artifact.upload_status == UploadStatus.ready,
            Artifact.scan_status == ScanStatus.not_scanned,
        ),
    )

    # 逼近配額:比率在 Python 端算,因為上限依級距而異(政策只存在 quota.py)。
    # 專案數在本平台規模下不會多到需要下推到 SQL;真的多了再說(設計文件 §10)。
    projects = (await session.execute(select(Project))).scalars().all()
    quota_warnings = []
    for project in projects:
        limit = limit_for(settings, project.quota_tier)
        if limit and project.total_bytes >= limit * QUOTA_WARN_RATIO:
            quota_warnings.append(
                ProjectUsage(
                    slug=project.slug,
                    name=project.name,
                    used_bytes=project.total_bytes,
                    limit_bytes=limit,
                    tier=project.quota_tier.value,
                )
            )
    quota_warnings.sort(key=lambda p: p.percent, reverse=True)

    # 停滯 draft:界線在 Python 端算好再帶進 WHERE——SQLite(測試)與
    # PostgreSQL(正式)的「間隔」語法不同,把它寫進 SQL 會綁死其中一種。
    stale_before = datetime.now(UTC) - timedelta(days=STALE_DRAFT_DAYS)
    rows = (
        await session.execute(
            select(Release, Project)
            .join(Project, Release.project_id == Project.id)
            .where(Release.status == ReleaseStatus.draft, Release.created_at <= stale_before)
            .order_by(Release.created_at)
            .limit(TOP_N)
        )
    ).all()
    now = datetime.now(UTC)
    stale_drafts = [
        StaleDraft(
            project_slug=project.slug,
            project_name=project.name,
            version=release.version,
            # created_at 在 SQLite 可能是 naive,補上 UTC 再相減,避免整頁 500。
            days=(now - _aware(release.created_at)).days,
        )
        for release, project in rows
    ]

    orphan_rows = (
        await session.execute(
            select(Project, User)
            .join(User, Project.owner_id == User.id)
            .where(User.status == UserStatus.disabled)
            .order_by(Project.slug)
            .limit(TOP_N)
        )
    ).all()
    orphan_projects = [
        OrphanProject(slug=project.slug, name=project.name, owner_sub=owner.sub)
        for project, owner in orphan_rows
    ]

    open_issues = await _scalar(
        session,
        select(func.count())
        .select_from(Issue)
        .where(Issue.status.in_((IssueStatus.open, IssueStatus.in_progress))),
    )

    pending_reviews = await _scalar(
        session,
        select(func.count())
        .select_from(Release)
        .where(Release.status == ReleaseStatus.pending_review),
    )

    return Todos(
        pending_users=kpi_pending,
        open_issues=open_issues,
        pending_reviews=pending_reviews,
        not_scanned_artifacts=not_scanned,
        quota_warnings=quota_warnings[:TOP_N],
        stale_drafts=stale_drafts,
        orphan_projects=orphan_projects,
    )


def _aware(value: datetime) -> datetime:
    """把可能沒有時區的 datetime 補成 UTC。

    SQLite(測試)不保存時區,PostgreSQL(正式)保存;相減時混用會 TypeError,
    而那會讓整個管理頁 500——這種環境差異要在讀取端一次收掉。
    """
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def human_bytes(value: int) -> str:
    """把位元組轉成人看得懂的字串(顯示用,不參與任何計算)。"""
    step = 1024.0
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < step or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= step
    return f"{amount:.1f} TB"
