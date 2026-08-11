"""T87 OIDC 失敗時記下 IdP 的 `error` 碼。

起因:兩次登入故障排查時,我方 log 只有 `{"message": "授權碼交換失敗", "status": 400}`
——`invalid_grant`(code 用過/過期/PKCE 不符/redirect_uri 不一致)與
`invalid_client`(secret 錯)在 log 裡長得一模一樣,**只能靠猜**。

原本的註解寫「不記進 log(可能含 token)」。前半對,後半過度保守:
token 端點的**錯誤**回應依 RFC 6749 §5.2 只有 `error` / `error_description` /
`error_uri`,**不會有 token**(有 token 的是 200 的成功回應,那條路徑本來就不記)。

🔴 本檔釘住的四條界線:

1. **只記 `error`(列舉值),不記 `error_description`(自由字串)。**
   後者由 IdP 決定內容,我方無法保證裡面不會出現使用者名稱或請求片段。
2. **log 不得出現任何 token**,即使 body 裡有(防禦性:不該發生,但要釘住)。
3. **body 不是 JSON 時不得炸**——IdP 可能回 HTML 錯誤頁(被 gateway 擋下時就會)。
   記 log 這件事本身不能變成新的例外。
4. **回應給呼叫端的文字一字不改**:錯誤碼是維運資訊,不是給瀏覽器看的。
"""

import logging

import httpx
import pytest

from app.oidc import Discovery, OidcClient
from app.problems import ProblemError

TOKEN_URL = "https://idp.example.test/token"


class _Oidc(OidcClient):
    """把「打網路」換成固定回應;組請求與處理回應的邏輯仍是受測對象。"""

    def __init__(self, settings, response: httpx.Response) -> None:
        super().__init__(settings)
        self._response = response

    async def load_discovery(self, force: bool = False) -> Discovery:
        return Discovery(
            issuer="https://idp.example.test",
            authorization_endpoint="https://idp.example.test/auth",
            token_endpoint=TOKEN_URL,
            jwks_uri="https://idp.example.test/certs",
        )


def _client(settings, monkeypatch, response: httpx.Response) -> OidcClient:
    async def _post(self, url, **kwargs):
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    return _Oidc(settings, response)


def _json(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("POST", TOKEN_URL))


async def test_交換失敗時記下error碼(settings, monkeypatch, caplog):
    oidc = _client(
        settings,
        monkeypatch,
        _json(400, {"error": "invalid_grant", "error_description": "Code not valid"}),
    )

    with caplog.at_level(logging.WARNING), pytest.raises(ProblemError):
        await oidc.exchange_code("the-code", "the-verifier")

    record = next(r for r in caplog.records if "授權碼交換失敗" in r.getMessage())
    assert getattr(record, "oidc_error", None) == "invalid_grant"


async def test_不記error_description(settings, monkeypatch, caplog):
    """🔴 自由字串由 IdP 決定內容,可能夾帶使用者名稱或請求片段。"""
    secret_ish = "user benny@example.com token abc.def.ghi"
    oidc = _client(
        settings, monkeypatch, _json(400, {"error": "invalid_grant", "error_description": secret_ish})
    )

    with caplog.at_level(logging.WARNING), pytest.raises(ProblemError):
        await oidc.exchange_code("the-code", "the-verifier")

    blob = "".join(f"{r.getMessage()}{r.__dict__}" for r in caplog.records)
    assert secret_ish not in blob
    assert "benny@example.com" not in blob


async def test_即使body夾帶token也不得入log(settings, monkeypatch, caplog):
    """🔴 防禦性:非 200 不該有 token,但萬一有,也絕不能被記下來。"""
    oidc = _client(
        settings,
        monkeypatch,
        _json(400, {"error": "invalid_grant", "access_token": "leaked-token-value"}),
    )

    with caplog.at_level(logging.WARNING), pytest.raises(ProblemError):
        await oidc.exchange_code("the-code", "the-verifier")

    blob = "".join(f"{r.getMessage()}{r.__dict__}" for r in caplog.records)
    assert "leaked-token-value" not in blob


async def test_body不是JSON也不炸(settings, monkeypatch, caplog):
    """IdP 或 gateway 可能回 HTML 錯誤頁;記 log 不能變成新的例外。"""
    html = httpx.Response(
        502, text="<html>bad gateway</html>", request=httpx.Request("POST", TOKEN_URL)
    )
    oidc = _client(settings, monkeypatch, html)

    with caplog.at_level(logging.WARNING), pytest.raises(ProblemError):
        await oidc.exchange_code("the-code", "the-verifier")

    record = next(r for r in caplog.records if "授權碼交換失敗" in r.getMessage())
    assert record.status == 502


async def test_續期失敗也記下error(settings, monkeypatch, caplog):
    """`refresh` 原本完全沒有 log——續期失敗時同樣查無可查。"""
    oidc = _client(settings, monkeypatch, _json(400, {"error": "invalid_grant"}))

    with caplog.at_level(logging.WARNING), pytest.raises(ProblemError):
        await oidc.refresh("the-refresh-token")

    record = next(r for r in caplog.records if "續期失敗" in r.getMessage())
    assert getattr(record, "oidc_error", None) == "invalid_grant"


async def test_回應給呼叫端的文字未變(settings, monkeypatch):
    """🔴 錯誤碼是維運資訊,不是給瀏覽器看的——洩漏 client 設定細節無益。"""
    oidc = _client(settings, monkeypatch, _json(400, {"error": "invalid_client"}))

    with pytest.raises(ProblemError) as exc:
        await oidc.exchange_code("the-code", "the-verifier")

    assert exc.value.detail == "授權碼交換失敗,請重新登入。"
    assert "invalid_client" not in exc.value.detail
