"""每個帳號的單檔上限(T141;計畫書 `docs/plans/設計_每個帳號的單檔上限.md`)。

**上限的對應只存在這個模組**,比照 `quota.py` 的做法:三個生效點
(Content-Length 預檢、串流上限、上傳頁的前端預檢值)呼叫同一個函式,
否則三條路徑的判準必然漂移,而漂移的症狀是「畫面說可以但傳不上去」。

🔴 這件事有三道牆,由外而內:

    gateway client_max_body_size   ← nginx,不在本 repo(cats-portal)
            ↓ 通過才到得了 App
    MAX_ARTIFACT_BYTES             ← 全站上限(.env)
            ↓
    users.max_artifact_bytes       ← 帳號個別上限(本模組)

**App 看不到 gateway 的設定**(另一個 repo、另一個容器),所以「不得超過
gateway」這件事只能靠 `MAX_ARTIFACT_BYTES` 這個代理值 —— 而它本來就被
`.env.example` 檔頭那條紅線要求 ≤ gateway。刻意用既有的紀律當防線,
不新發明一個。
"""

from .config import Settings


def effective_artifact_limit(settings: Settings, user: object) -> int:
    """回傳這個帳號實際適用的單檔上限(bytes)。

    參數:settings 設定值、user 有 `max_artifact_bytes` 屬性的使用者。
    回傳:bytes。副作用:無(純函式)。

    - `None` → **沿用全站上限**(既有帳號一律如此,行為逐字不變)
    - 有值 → 取**兩者的較小者**

    🔴 **為什麼是 `min` 而不是直接用帳號的值:** 全站上限**事後**被調小時
    (例如磁碟吃緊),個別帳號的舊設定不該繞過它 ——
    少了這個 `min`,「把全站上限調小」這個動作會**靜默地對某些帳號無效**,
    而那正是最需要它生效的時候。
    """
    personal = getattr(user, "max_artifact_bytes", None)
    if personal is None:
        return settings.max_artifact_bytes
    return min(personal, settings.max_artifact_bytes)


def is_personal_limit(settings: Settings, user: object) -> bool:
    """這個帳號目前生效的上限,是不是因為「他被單獨設定過」才這麼小。

    用途:錯誤訊息要分得出是帳號限制還是全站限制 —— 否則使用者問
    「為什麼我只能傳這麼小」時,沒有人分得出他是被單獨設定過、
    還是全站就這麼小。副作用:無。
    """
    personal = getattr(user, "max_artifact_bytes", None)
    return personal is not None and personal < settings.max_artifact_bytes
