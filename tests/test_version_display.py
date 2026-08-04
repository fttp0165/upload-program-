"""T68:首頁顯示版本號。

為什麼要有這一條:確認「線上跑的是哪一版」原本只能挑一個「只有新版才有」的
靜態檔當哨兵(runbook §A.4)——繞路且每次換版都要重挑。版本號印在首頁上,
使用者回報問題與我方換版驗證都能一眼看到。

🔴 單一真相:`app.version.APP_VERSION`。`main.py` 的 OpenAPI version 也讀同一個常數
——原本那裡寫死 `0.1.0`,已經與現實脫節 5 個版本,正是「同一件事寫兩份」的必然結果。
"""

import re

from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


async def test_匿名者看得到版本號(client):
    """T81:匿名瀏覽器不再停在首頁,版本號改在 /help 驗——版號在共用頁尾,載體不影響意義。"""
    from app.version import APP_VERSION

    resp = await client.get("/help", headers=BROWSER)
    assert resp.status_code == 200
    assert f"v{APP_VERSION}" in resp.text


async def test_已開通者首頁也顯示版本號(client, app, oidc):
    from app.version import APP_VERSION

    await make_user(app, "sub-version-user")
    resp = await client.get("/", headers={**BROWSER, **auth(oidc.issue("sub-version-user"))})
    assert f"v{APP_VERSION}" in resp.text


async def test_OpenAPI版本與同一個常數一致(client):
    """🔴 防漂移:寫死在 main.py 的版本號曾經停在 0.1.0 五個版本。"""
    from app.version import APP_VERSION

    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    assert resp.json()["info"]["version"] == APP_VERSION


async def test_版本字串格式受檢():
    """`vX.Y.Z` 是 tag 的形狀;首頁顯示與 tag 對得上,換版驗證才有意義。"""
    from app.version import APP_VERSION

    assert re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION), f"版本字串應為 X.Y.Z:{APP_VERSION!r}"
