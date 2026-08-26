"""送審通知信(T102;SSO 契約 §4.2b)。

**它要解決的事**:平台沒有站內推播,作者送審之後,管理員只有主動看後台才知道
有東西要審。Benny 2026-08-26 裁示:通知走 email,信箱跟 Keycloak 要——
契約 §4.2a 第 1 條禁止 App 呼叫 Admin API,所以唯一合規的路是 §4.2b:
管理員**本人登入時**的 token 帶 `email` claim,落地為 `users.notify_email_cache`
(見 security.upsert_user),寄信時只讀這份快取。

§4.2b 在本模組落實的條文:
- 第 4 條:信件內容**只含專案 slug、版本號與後台連結**,不含任何使用者資料;
  log 只記 sub 與略過原因,**絕不記信箱**。
- 第 6 條:缺 email 的收件人略過並記一行 log;**寄失敗只記 log,絕不阻斷送審**。
- 第 8 條:收件人=已明示訂閱者(`review_email_opt_in=true`,預設關)。

🔴 空窗期(§4.2b 第 10 條義務,寫進文件不寫進幻想):管理員在 IdP 改信箱後,
本服務的快取**最遲要到他下次登入才更新**,期間通知寄往舊信箱且寄件端顯示成功;
離職管理員的信箱若停用或轉交,通知會寄給接手的人。緩解=信裡不含使用者資料,
寄錯人洩漏的只有「有東西待審」;平台側則是離職一律停用不刪除。
"""

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import PlatformRole, Release, User, UserStatus

log = logging.getLogger(__name__)


class Mailer:
    """薄薄一層 SMTP:標準庫 smtplib,不加相依。

    用途:寄純文字通知信。參數:settings(smtp_host/port/from/timeout)。
    副作用:對 SMTP 主機發出連線。`smtp_host` 或 `smtp_from` 為空時 `enabled=False`,
    `send()` 一律不該被呼叫(呼叫端先看 enabled)。
    """

    def __init__(self, settings: Settings) -> None:
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._from = settings.smtp_from
        self._timeout = settings.smtp_timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self._host and self._from)

    def _send_sync(self, recipients: list[str], subject: str, body: str) -> None:
        message = EmailMessage()
        # 🔴 From 只放位址,不帶顯示名稱(Exchange 靜默丟棄帶顯示名稱的 From,
        # 契約 §5.3 坑表)。收件人放 To 逐一列出即可——收件者都是平台管理員,
        # 彼此知道對方是管理員,不構成洩漏。
        message["From"] = self._from
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
            smtp.send_message(message)

    async def send(self, recipients: list[str], subject: str, body: str) -> None:
        """寄一封信。smtplib 是阻塞 IO,丟進執行緒跑,不卡 event loop。"""
        await asyncio.to_thread(self._send_sync, list(recipients), subject, body)


async def notify_review_submitted(
    session: AsyncSession, mailer, settings: Settings, release: Release
) -> None:
    """送審當下通知已訂閱的管理員。

    參數:session(唯讀查收件人)、mailer(Mailer 或測試替身)、settings、release
    (需已載入 project)。回傳:無。
    副作用:寄信、寫 log。🔴 **任何失敗都吞掉只記 log**——通知炸掉不得讓作者的
    送審跟著失敗(§4.2b 第 6 條精神);這裡是整條流程唯一允許 except Exception 的地方。
    """
    try:
        rows = (
            await session.execute(
                select(User.sub, User.notify_email_cache).where(
                    User.status == UserStatus.active,
                    User.platform_role == PlatformRole.admin,
                    User.review_email_opt_in.is_(True),
                )
            )
        ).all()
        recipients = [email for _, email in rows if email]
        # 第 6 條:缺 email 不阻斷,但要留下「為什麼這個人沒收到」的痕跡(記 sub 不記信箱)。
        for sub, email in rows:
            if not email:
                log.info(
                    "送審通知略過收件人",
                    extra={"sub": sub, "reason": "no-verified-email-cache"},
                )
        if not recipients:
            log.info("送審通知無人收件", extra={"release_id": str(release.id)})
            return
        if not mailer.enabled:
            log.info("SMTP 未設定,送審通知停用", extra={"release_id": str(release.id)})
            return

        slug, version = release.project.slug, release.version
        subject = f"[upload-program] 待審版本:{slug} {version}"
        body = (
            f"專案 {slug} 的版本 {version} 已送審,等待核准。\n\n"
            f"審核佇列:{settings.external_base}/admin/reviews\n\n"
            "(這封信由 upload-program 自動寄出;取消訂閱請到審核佇列頁關閉通知。)"
        )
        await mailer.send(recipients, subject, body)
        log.info(
            "送審通知已寄出",
            extra={"release_id": str(release.id), "recipients": len(recipients)},
        )
    except Exception:
        log.exception("送審通知寄送失敗(不影響送審)", extra={"release_id": str(release.id)})
