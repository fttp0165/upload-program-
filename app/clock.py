"""台灣時區換算(T146)。

🔴 業務資料一律存 **UTC**(`models._now()`)——這是對的,平台的 gateway /
Keycloak log 也是 UTC,跨系統對時要同一個時區才對得起來。**錯的是顯示層**:
模板原本直接對 UTC 的 datetime 呼叫 `strftime()`,畫面上印出的是 UTC 的
時刻數字、不帶任何標示——對一個全公司都在台灣的平台,使用者看到
「最後更新 05:54」只會讀成台北時間,而實際是台北時間 13:54(2026-10-08
Benny 截圖回報的正是這個)。

本模組只做一件事:把「儲存用的 UTC」換算成「人看的台北時間」,給
`templating.py` 的 Jinja filter 用。**換算只在顯示的那一刻發生**,
資料庫裡的值完全不動——這不是資料遷移,是一個顯示層的函式。
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

# 本平台只服務台灣境內的公司內部使用者(CLAUDE.md:「公司內部開發者程式
# 分享平台」),固定台北時區,不做使用者個別時區設定——平台沒有這個需求,
# 硬做就是過度工程。
TAIPEI = ZoneInfo("Asia/Taipei")


def aware(value: datetime) -> datetime:
    """把可能沒有時區的 datetime 補成 UTC。

    SQLite(測試)不保存時區,PostgreSQL(正式)保存;兩者混用去相減或換算
    時區,輕則 TypeError、重則換算出錯的結果。這種環境差異要在讀取端一次
    收掉。

    🔴 單一真相:`dashboard.py` 原本有一份幾乎一樣的 `_aware()`(T70 寫的,
    專治這個環境差異),本次搬來這裡讓時區換算也能共用同一份——兩個地方
    各自補時區,遲早有一邊漏掉而沒人發現。
    """
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def taipei(value: datetime) -> datetime:
    """把一個(可能沒有時區的)datetime 換成台北時間。副作用:無。"""
    return aware(value).astimezone(TAIPEI)
