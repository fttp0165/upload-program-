"""專案短名(slug)的自動產生(T96)。

為什麼要有這支模組:原本建立專案的第一個欄位就是必填的 `slug` —— 使用者要建專案,
第一件事卻是先想一個網址用的英文短名。那是實作細節,不是他要做的事。

🔴 **產生的短名一律要通過 `schemas.SLUG_RE`**:自動產生不是放寬規則的藉口,
它進的是同一個欄位、同一條網址(而本平台**沒有改名功能**,slug 一旦出現在別人
貼出去的連結裡就永久有效)。

🔴 **刻意不引入拼音/音譯套件**:那是新的執行期相依 + 供應鏈風險,而本專案已有
「自己寫 `markdown_lite` 而不引入 markdown + 消毒器兩個相依」的前例。代價是
純中文名稱只能退回不可讀的 `p-xxxxxxxx` —— 這個代價寫在 dev-log T96 裡,不藏著。
"""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Project

# slug 的長度上限與 SLUG_RE 一致;留 8 字元給撞名後綴(`-2`…`-999`)與退路短名。
_MAX_LEN = 56
_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """把專案名稱轉成網址用短名;沒有可用字元時回空字串。

    參數:name 專案名稱。回傳:小寫英數與連字號的短名,或 `""`。副作用:無。

    🔴 **規則必須穩定**:同樣的名稱要得到同樣的結果(不是每次亂數)——
    slug 會出現在別人貼出去的連結裡。回空字串是**刻意的**:
    「沒有可用 ASCII」與「隨便給一個」是兩件事,由呼叫端決定退路(見 `fallback_slug`)。
    """
    lowered = (name or "").strip().lower()
    # 非 [a-z0-9] 的一律折成單一連字號:中日韓文字、emoji、底線、空白都走這條。
    candidate = _NON_SLUG.sub("-", lowered).strip("-")[:_MAX_LEN].strip("-")
    # SLUG_RE 要求至少 3 字元;不足就當作沒有可用短名,不硬湊。
    return candidate if len(candidate) >= 3 else ""


def fallback_slug() -> str:
    """名稱無可用 ASCII 時的退路短名(`p-` + 8 碼十六進位)。

    回傳:合法 slug。副作用:無(每次呼叫都不同——它就是拿來避開撞名的)。

    ⚠ 網址會變成 `p-3f9c2a10` 這種不可讀的形式。這是「不引入拼音套件」的**已知代價**,
    不是疏漏;使用者若在意,可在專案名稱裡放英文。
    """
    return f"p-{uuid.uuid4().hex[:8]}"


async def unique_slug(session: AsyncSession, name: str) -> str:
    """替新專案挑一個沒被用過的短名。

    參數:session、name 專案名稱。回傳:可用的 slug。副作用:唯讀查詢。

    🔴 撞名時**自動加後綴**(`-2`、`-3`…)而不是把錯誤丟回使用者:
    表單已經沒有那個欄位了,叫他「換一個短名」是叫他改一個看不到的東西。

    ⚠ 這裡的查詢**不能取代** DB 的 UNIQUE 約束:兩個請求同時挑到同一個名字時,
    先寫入的贏、後者拿到 IntegrityError,由呼叫端重試(見 `web.py` / `projects.py`)。
    查詢只是讓「常見情況」得到可讀的短名。
    """
    base = slugify(name)
    if not base:
        return fallback_slug()

    taken = set(
        (await session.execute(select(Project.slug).where(Project.slug.like(f"{base}%"))))
        .scalars()
        .all()
    )
    if base not in taken:
        return base
    # 從 2 開始:第一個佔用者就是 base 本身,`-1` 會讓人以為還有個 `-0`。
    for n in range(2, 1000):
        candidate = f"{base}-{n}"
        if candidate not in taken:
            return candidate
    return fallback_slug()
