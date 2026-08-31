"""T113 session cookie「存在但無法解析」要留下痕跡。

起因:portal v2.0 回函的現場重現裡,登入成功 23 秒後被踢回登入——
若當下 cookie 有送達但簽章壞/內容毀,現行 `read_session` **靜默回 None**,
我方 log 一片空白,事後完全無從分辨「沒帶 cookie」與「帶了但讀不動」。

🔴 兩條界線:

1. **log 不得含 cookie 原文**——session cookie 內含 access/refresh token,
   記內容等於把 token 寫進 log(紅線:log 不記完整 JWT)。只記長度。
2. **cookie 不存在不記**:匿名瀏覽是常態,不是異常;記了會把 log 灌爆,
   真正的異常反而被淹掉。
"""

import logging

from tests.conftest import make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


async def test_壞簽章的session_cookie要留警告且不含原文(client, app, caplog):
    await make_user(app, "sub-t92")
    bogus = "not-a-valid-signed-session-cookie-value-xxxxxxxx"
    name = app.state.settings.session_cookie_name

    with caplog.at_level(logging.WARNING):
        resp = await client.get(
            "/help", headers=BROWSER, cookies={name: bogus}, follow_redirects=False
        )

    assert resp.status_code == 200  # 行為不變:視為未登入,不是 500
    record = next(
        (r for r in caplog.records if "session cookie 無法解析" in r.getMessage()), None
    )
    assert record is not None, "壞 cookie 必須留下痕跡——否則 23 秒踢回這類事永遠查不了"
    blob = f"{record.getMessage()}{record.__dict__}"
    assert bogus not in blob, "🔴 log 不得含 cookie 原文(內含 token)"


async def test_沒有cookie不記警告(client, app, caplog):
    """匿名是常態不是異常;記了會把真正的異常淹掉。"""
    client.cookies.clear()
    with caplog.at_level(logging.WARNING):
        resp = await client.get("/help", headers=BROWSER, follow_redirects=False)

    assert resp.status_code == 200
    assert not any("session cookie 無法解析" in r.getMessage() for r in caplog.records)
