"""判定「哪些專案疑似重複建立」(T134)。

🔴 為什麼需要判定而不是列出全部讓人自己看:重複是 **T96 撞名自動加後綴**的
必然產物 —— 使用者按了兩次「建立」,第二個的短名自動變成 `-2`,而兩個名稱一模一樣。
全站專案一多,那兩列不會相鄰,肉眼掃不出來。

本模組只做**純函式**的分組,不查資料庫 —— 呼叫端負責把專案撈出來。
"""

from collections.abc import Iterable, Sequence

from .slugs import slugify


def group_key(name: str) -> str:
    """一個專案名稱的分組鍵。

    參數:name 專案名稱。回傳:用於比對的正規化字串。副作用:無。

    🔴 **用名稱而不是「把 slug 的 `-N` 後綴去掉」**,雖然後者更直覺:
    那會把 `Tool` 與 `Tool 2` 判成同一組 —— 而 `Tool 2` 是**使用者自己取的名字**,
    不是系統加的後綴。系統加的後綴只出現在 slug 上,**名稱一字未改**;
    所以用名稱判定同時抓得到真重複、又不會誤判刻意的第二代。
    刪除是不可逆的,誤判的代價不對稱。

    🔴 `slugify()` 為空時退回名稱本身(casefold 後),**不是退回空字串**:
    純中文名稱的 `slugify()` 一律回 `""`(T96 已載明),拿 `""` 當鍵會讓
    **全站所有中文名稱的專案歸成一大組** —— 那不是提示,是災難。
    """
    return slugify(name) or " ".join(name.split()).casefold()


def duplicate_groups(projects: Iterable) -> list[list]:
    """把疑似重複的專案分組;每組 ≥2 個,依建立時間由舊到新。

    參數:projects 專案物件(需有 `name` 與 `created_at`)。
    回傳:分組清單;組內第一個是最早建立的那一個。副作用:無。

    只有 ≥2 個的組會回傳 —— 一個專案自己不構成重複。
    組的順序依「最早建立時間」由新到舊:剛剛才誤建的那一組最需要處理。
    """
    buckets: dict[str, list] = {}
    for project in projects:
        buckets.setdefault(group_key(project.name), []).append(project)

    groups = [sorted(items, key=lambda p: p.created_at) for items in buckets.values() if len(items) > 1]
    groups.sort(key=lambda items: items[0].created_at, reverse=True)
    return groups


def flatten(groups: Sequence[Sequence]) -> set:
    """分組裡所有專案的 id 集合,供模板判斷某一列要不要標記。"""
    return {project.id for group in groups for project in group}
