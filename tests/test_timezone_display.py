"""T146:畫面上的時間戳要顯示台北時間,不是裸 UTC。

起因:Benny 截圖回報專案頁「最後更新 2026-10-08 05:54」,問「確認是為台北
時間嗎」。答案:不是——那是 UTC,直接印出來、不帶任何標示。models.py 的
`_now()` 一律存 UTC 是對的(與平台 gateway / Keycloak log 同一時區),
錯的是模板直接對 UTC 值呼叫 `strftime()`,畫面上的數字會被全是台灣使用者
的本平台讀成台北時間,而實際差 8 小時。

🔴 本檔兩條最要緊的測試:

- `test_換算跨過午夜`——只測同一天內的加減法會漏掉「連日期都該跟著跳」
  這種更隱蔽的錯(UTC 20:00 要變成台北**隔天** 04:00)。
- `test_專案頁畫面上看不到未換算的UTC`——反向斷言,釘住**畫面上**沒有
  裸 UTC 字串;原始值仍留在 `title` 屬性裡(沿用 T144 立下的規矩),
  所以這條不是「HTML 原始碼裡完全不能出現」,而是「視覺上看不到」。
"""

import re
from datetime import UTC, datetime

from sqlalchemy import select

from app.clock import taipei
from app.models import Project
from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}

_TAGS = re.compile(r"<[^>]*>")


def _visible(html: str) -> str:
    """只留下畫面上直接看得到的文字,剝掉所有標籤(連同屬性,含 title)。

    與 `test_ui_readability.py` 的 `visible()` 同一個理由:直接對整份 HTML
    掃字串,會連刻意留在 `title` 屬性裡的原始值都一起咬到。
    """
    return _TAGS.sub(" ", html)


def test_taipei把UTC轉成台北時間():
    utc = datetime(2026, 10, 8, 5, 54, tzinfo=UTC)
    assert taipei(utc).strftime("%Y-%m-%d %H:%M") == "2026-10-08 13:54"


def test_換算跨過午夜():
    """🔴 UTC 當天 20:00,台北已經是隔天 04:00——連日期都要跟著跳。"""
    utc = datetime(2026, 10, 7, 20, 0, tzinfo=UTC)
    assert taipei(utc).strftime("%Y-%m-%d %H:%M") == "2026-10-08 04:00"


def test_沒有時區的datetime視為UTC再換算():
    """SQLite(測試環境)不保存時區,讀出來是 naive——視為 UTC 而不是炸例外。"""
    naive = datetime(2026, 10, 8, 5, 54)
    assert taipei(naive).strftime("%Y-%m-%d %H:%M") == "2026-10-08 13:54"


def test_taipei_time_filter已註冊且換算正確():
    from app.templating import _env

    fn = _env.filters.get("taipei_time")
    assert fn is not None, "taipei_time 沒有註冊成 Jinja filter"
    utc = datetime(2026, 10, 7, 20, 0, tzinfo=UTC)
    assert fn(utc) == "2026-10-08 04:00"


async def test_專案頁畫面上看不到未換算的UTC(client, app, oidc):
    await make_user(app, "sub-tz")
    token = oidc.issue("sub-tz")
    resp = await client.post(
        "/v1/projects",
        json={"slug": "tz-tool", "name": "時區測試", "summary": ""},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text

    # 故意選一個跨午夜的 UTC 時刻,而不是隨便一個——這樣「有沒有真的換算」
    # 與「只是剛好同一天」才分得開。
    known_utc = datetime(2026, 10, 7, 20, 0, tzinfo=UTC)
    async with app.state.sessionmaker() as session:
        project = (
            await session.execute(select(Project).where(Project.slug == "tz-tool"))
        ).scalar_one()
        project.created_at = known_utc
        project.updated_at = known_utc
        await session.commit()

    resp = await client.get("/projects/tz-tool", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200, resp.text
    body = resp.text
    visible = _visible(body)

    assert "2026-10-08 04:00" in visible, "畫面該顯示換算後的台北時間"
    assert "2026-10-07 20:00" not in visible, "畫面上不該看得到未換算的 UTC 時刻"
    # 原始 UTC 值仍要保留(供對照平台其他系統的 log),只是移進 title 屬性。
    assert "2026-10-07T20:00:00" in body, "原始 UTC 值應保留在 title 屬性供對照"
