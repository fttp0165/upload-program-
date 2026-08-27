"""寄信(T99):問題回報通知管理員。

🔴 **不引入任何相依** —— `smtplib` / `email` 都是標準庫,比照本專案自寫
`markdown_lite` 而不引入 markdown + 消毒器兩個相依的前例。

🔴 **預設關閉**(`MAIL_ENABLED=false`):沒設好 SMTP 就完全不寄,
而不是每次有人回報就在 log 裡噴一次錯 —— 那種噪音會讓真的錯誤被忽略。

🔴 **寄信失敗不得阻斷業務流程**(Benny 2026-08-25 裁示 + 契約 §4.2a L1b 第 16 條):
使用者親手寫的回報內容,不能因為 SMTP 掛掉而丟失。所以呼叫端在**回應之後**
(FastAPI `BackgroundTasks`)才寄,而本模組的 `send()` 只回 True/False,不拋。

🔴 **收件地址不得進 log**(L1b 第 5 條):log 只記「寄了幾封、失敗的例外型別」。
log 是最容易漏的出口——它不在畫面上,沒人會去看。
"""

import logging
import smtplib
from email.message import EmailMessage

from .config import Settings

log = logging.getLogger("mail")

# 內部 relay 也可能卡住;10 秒是「使用者早就拿到回應了,但別讓 worker 永遠佔著」的折衷。
_TIMEOUT_SECONDS = 10


class Mailer:
    """SMTP 寄件者。以物件掛在 `app.state.mailer`,測試才能換成替身。

    參數:settings(讀 `mail_enabled` / `smtp_host` / `smtp_port` / `mail_from`)。
    副作用:`send()` 連線到 SMTP relay。
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        """設定齊備且明確開啟才算啟用(缺 host 時視同關閉,不要半開)。"""
        return bool(self._settings.mail_enabled and self._settings.smtp_host)

    def send(self, to: str, subject: str, body: str) -> bool:
        """寄一封純文字信;回傳是否成功。**絕不拋例外。**

        參數:to 單一收件地址、subject、body(純文字)。
        回傳:True 成功 / False 失敗或未啟用。副作用:SMTP 連線。

        🔴 一次一個收件人,不用 To 塞多人:管理員彼此看得到對方的信箱是
        沒必要的外洩面(L1b 的精神是「拿來投遞,不拿來給人看」)。
        """
        if not self.enabled:
            return False

        message = EmailMessage()
        message["From"] = self._settings.mail_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        try:
            # 公司內部 relay:不需帳密、不用 TLS(Benny 2026-08-25 裁示)。
            with smtplib.SMTP(
                self._settings.smtp_host, self._settings.smtp_port, timeout=_TIMEOUT_SECONDS
            ) as smtp:
                smtp.send_message(message)
            return True
        except Exception as exc:
            # 🔴 只記例外型別,不記地址、不記內容。
            log.warning("寄信失敗", extra={"error": type(exc).__name__})
            return False
