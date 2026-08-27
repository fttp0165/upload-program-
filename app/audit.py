"""稽核紀錄的唯一入口(T38 / F54)。

🔴 **為什麼要有這個模組**:若每個 router 各自 `session.add(AuditEvent(...))`,
action 字串遲早會出現三種寫法(`project.create` / `project_create` / `create_project`),
而稽核的查詢是靠字串比對的——字彙一分岔,查詢就會安靜地漏掉紀錄。
這是本專案反覆出現的「同一件事只能有一條路」(`_download_response`、`query_projects`、
`project_out`、`problem_response`)的第五次應用。

🔴 **`record()` 同時寫 stdout log**:原本各處已有 `log.info("建立專案", ...)`。
如果稽核另外寫一份,就會出現「log 記了但 audit 沒記」的分岔,而那種分岔在事後
追查時最難發現。所以兩者由同一次呼叫產生。

🔴 **寫入與業務操作共用同一個 session**(不自己 commit):
呼叫端的 `commit()` 一起送出,業務 rollback 時稽核也一起消失。
這保證了「**有紀錄 ⇔ 事情真的發生了**」。反例是用獨立 session 寫稽核——
那會留下「記了但沒發生」的假紀錄,而一張混進假紀錄的稽核表就不能用了。
代價:失敗的嘗試不進稽核表(它們仍在 stdout log)。F54 要的是「誰做了什麼」,
不是入侵偵測。
"""

import enum
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging_setup import trace_id_var
from .models import AuditEvent

log = logging.getLogger(__name__)


class AuditAction(enum.StrEnum):
    """稽核動作的**唯一字彙來源**。

    命名格式固定為 `<目標>.<動作>`(小寫、底線),由 test_audit.py 釘住。
    新增被稽核的動作只要在這裡加一行——刻意讓它不需要 migration,
    因為阻力會讓人選擇不記,而漏記不會留下任何跡象。
    """

    # 帳號(F54:「開通了誰」)
    user_activate = "user.activate"
    user_disable = "user.disable"
    user_set_role = "user.set_role"

    # 專案(F54:「建了/刪了什麼」)
    project_create = "project.create"
    project_delete = "project.delete"
    project_transfer_owner = "project.transfer_owner"
    project_set_quota = "project.set_quota"
    # T100:發布者自行改可見性(internal ↔ private)。
    # 🔴 這個動作會改變「誰看得到」,所以它必須留痕 —— 事後問「這個專案什麼時候
    #    變成公開的」時,沒有紀錄就等於沒有答案。
    project_set_visibility = "project.set_visibility"
    member_set = "member.set"
    member_remove = "member.remove"

    # 版本與檔案(F54:「建了/刪了什麼」「上傳與下載了什麼」)
    release_create = "release.create"
    release_publish = "release.publish"
    release_delete = "release.delete"
    artifact_upload = "artifact.upload"
    artifact_delete = "artifact.delete"
    artifact_download = "artifact.download"

    # 問題回報(T77):回報是使用者主動留下的紀錄,狀態變更則是我方的處置——
    # 兩者都要能事後說明「誰在何時做了什麼」。
    issue_create = "issue.create"
    issue_comment = "issue.comment"
    issue_status_change = "issue.status_change"
    issue_attachment_upload = "issue.attachment_upload"


def record(
    session: AsyncSession,
    *,
    action: AuditAction,
    actor_id: uuid.UUID | None,
    target_type: str,
    target_id: uuid.UUID | None = None,
    target_label: str = "",
) -> AuditEvent:
    """把一筆稽核事件掛進**呼叫端的 session**,並同步寫一行 stdout log。

    參數:
      session      業務操作用的同一個 session(本函式**不 commit**)
      action       `AuditAction` 常數,不接受裸字串
      actor_id     操作者的本地 `user.id`(🔴 不存 email/姓名)
      target_type  目標種類:user / project / release / artifact
      target_id    目標的 UUID;目標可能之後被刪除,查不回去是正常的
      target_label 🔴 **人可讀的快照**(slug / version / filename)——
                   目標被刪掉之後,這是唯一還能說明「刪掉的是哪一個」的資訊。
                   只放識別用字串,不放使用者輸入的自由文字。

    回傳:尚未 flush 的 `AuditEvent`(呼叫端 commit 後才真正存在)。
    副作用:session.add()、一行 log。
    """
    event = AuditEvent(
        action=action.value,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label[:255],
        trace_id=trace_id_var.get(),
    )
    session.add(event)
    log.info(
        "稽核",
        extra={
            "audit_action": action.value,
            "actor_id": str(actor_id) if actor_id else "",
            "target_type": target_type,
            "target_id": str(target_id) if target_id else "",
            "target_label": event.target_label,
        },
    )
    return event


async def purge_expired(
    session: AsyncSession, retention_days: int, *, dry_run: bool = True
) -> int:
    """刪除超過保留期的稽核紀錄,回傳筆數。

    🔴 這支函式存在的理由是 **T37 開的支票**:T37 刻意不做下載事件表,
    當時的理由是「誰下載了什麼」屬於稽核,而稽核「有自己的保存期限與存取權限」。
    沒有清除手段的話,那句話就只是把一張無限長大的個資表推給未來。

    `dry_run=True`(預設)只數不刪——刪稽核紀錄是不可逆的,預設不該是刪。

    參數:session、retention_days(保留天數)、dry_run。
    回傳:符合條件的筆數(dry_run 時為「將會刪除」的筆數)。
    副作用:dry_run=False 時 DELETE + commit。
    """
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    condition = AuditEvent.occurred_at < cutoff

    count = (
        await session.execute(select(func.count()).select_from(AuditEvent).where(condition))
    ).scalar_one()
    if dry_run or count == 0:
        return count

    await session.execute(delete(AuditEvent).where(condition))
    await session.commit()
    log.info("清除過期稽核紀錄", extra={"removed": count, "retention_days": retention_days})
    return count
