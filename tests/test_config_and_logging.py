"""設定與可觀測性的規約測試。

這兩塊在 API 測試裡是「背景」,不會自然被覆蓋到,但都是平台鐵則:
- 缺必要環境變數必須 **fail-fast**,不得用預設值頂替
- log 必須是 **stdout 的 JSON 單行**,含 timestamp/level/service/message,且不含 JWT 或個資
- 收到 `X-Trace-Id` 要沿用,沒有就自己產生
"""

import json
import logging

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.logging_setup import JsonFormatter, trace_id_var

_REQUIRED = {
    "public_base_url": "https://catsapp.example.test",
    "api_prefix": "/upload",
    "database_url": "sqlite+aiosqlite:///:memory:",
    "oidc_issuer": "https://auth.example.test/realms/test",
    "oidc_client_id": "c",
    "oidc_client_secret": "not-a-real-secret",
    "session_secret": "not-a-real-session-secret",
    "s3_endpoint_url": "http://minio.invalid:9000",
    "s3_bucket": "b",
    "s3_access_key": "a",
    "s3_secret_key": "s",
}


def _settings(**overrides) -> Settings:
    return Settings(**{**_REQUIRED, **overrides})


@pytest.mark.parametrize("missing", sorted(_REQUIRED))
def test_缺任何必要變數都要fail_fast(missing, monkeypatch, tmp_path):
    """平台鐵則 10:缺必要變數時服務啟動失敗,而非用預設值頂替。"""
    # 隔絕真實環境與 .env,確保測的是「缺變數」而不是「剛好讀到了」
    for key in _REQUIRED:
        monkeypatch.delenv(key.upper(), raising=False)
    values = {k: v for k, v in _REQUIRED.items() if k != missing}
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=str(tmp_path / "nonexistent.env"), **values)
    assert missing in str(exc.value)


def test_連線池上限不得超過20():
    """共用 VM,連線吃光會拖垮鄰居。"""
    with pytest.raises(ValidationError):
        _settings(db_pool_size=21)
    assert _settings(db_pool_size=20).db_pool_size == 20


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("upload", "/upload"), ("/upload", "/upload"), ("/upload/", "/upload"), ("/", "")],
)
def test_路徑前綴正規化(raw, expected):
    assert _settings(api_prefix=raw).api_prefix == expected


# --- 容量上限(F34 / T33)-----------------------------------------------------

MB = 1024 * 1024
GB = 1024 * MB


def test_預設單檔上限為100MB():
    """Q7 裁示「50~100M」,取上限。"""
    assert _settings().max_artifact_bytes == 100 * MB


def test_預設專案容量為2GB():
    """Q10 裁示「一般員工開發的小工具 2G 就可以了」。"""
    assert _settings().max_project_bytes == 2 * GB


def test_env_example的容量值與程式預設一致():
    """🔴 防漂移:`config.py` 的預設與 `.env.example` 的示意值是兩份各自為政的數字。

    改了一邊忘了另一邊,部署時就會套用到錯的值,而且不會有任何錯誤訊息。
    這條測試讓兩者對不上時直接紅燈。
    """
    import pathlib
    import re

    text = pathlib.Path(".env.example").read_text(encoding="utf-8")
    # 值後面允許接註解(例:`MAX_ARTIFACT_BYTES=104857600  # 100 MB`),
    # 但仍要求整行只有「變數=數字[空白][註解]」,不接受 `=100abc` 這種殘缺值。
    declared = {
        key: int(value)
        for key, value in re.findall(
            r"^(MAX_\w+_BYTES)=(\d+)\s*(?:#.*)?$", text, flags=re.MULTILINE
        )
    }
    defaults = _settings()

    assert declared["MAX_ARTIFACT_BYTES"] == defaults.max_artifact_bytes
    assert declared["MAX_PROJECT_BYTES"] == defaults.max_project_bytes


def test_對外網址由設定組出而非從request推導():
    """TLS 在 gateway 終結,從 request 推導會得到 http://,所以一律用設定組。"""
    s = _settings()
    assert s.external_base == "https://catsapp.example.test/upload"
    # 子路徑部署下,redirect URI 必須含前綴(申請 client 時要登記這個)
    assert s.oidc_redirect_uri == "https://catsapp.example.test/upload/oidc/callback/"


def test_cookie_path綁自己的前綴():
    """避免與同主機其他 App 的 cookie 互蓋(接入指南 §6)。"""
    assert _settings().cookie_path == "/upload/"
    assert _settings(api_prefix="/").cookie_path == "/"


def test_issuer尾斜線會被去掉():
    """discovery 比對 issuer 時,多一個斜線就會誤判成不同 realm。"""
    assert _settings(oidc_issuer="https://auth.example.test/realms/test/").oidc_issuer.endswith(
        "test"
    )


# --- log ---------------------------------------------------------------------


def _format(record_kwargs: dict, **extra) -> dict:
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1, args=(), exc_info=None,
        **record_kwargs,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return json.loads(JsonFormatter("upload-program").format(record))


def test_log是JSON單行且含必要欄位():
    formatted = JsonFormatter("upload-program").format(
        logging.LogRecord("t", logging.INFO, __file__, 1, "建立專案", (), None)
    )
    assert "\n" not in formatted  # 單行:多行會讓 log 收集器把一筆拆成多筆
    payload = json.loads(formatted)
    assert {"timestamp", "level", "service", "message"} <= payload.keys()
    assert payload["service"] == "upload-program"
    assert payload["level"] == "info"


def test_log時間為ISO8601含時區():
    payload = _format({"msg": "x", "exc_text": None})
    stamp = payload["timestamp"]
    assert "T" in stamp
    assert stamp[-6] in "+-" or stamp.endswith("Z")  # 必須有時區位移


def test_log帶入trace_id():
    token = trace_id_var.set("trace-abc-123")
    try:
        assert _format({"msg": "x"})["trace_id"] == "trace-abc-123"
    finally:
        trace_id_var.reset(token)


def test_額外欄位會被帶進log():
    payload = _format({"msg": "上傳完成"}, artifact_id="a-1", size_bytes=123)
    assert payload["artifact_id"] == "a-1"
    assert payload["size_bytes"] == 123


async def test_trace_id沿用請求帶來的值(client):
    # 標頭值必須是 ASCII(HTTP 規範),這裡用上游服務會給的那種格式
    upstream = "trace-from-upstream-0001"
    resp = await client.get("/health", headers={"X-Trace-Id": upstream})
    assert resp.headers["X-Trace-Id"] == upstream


async def test_沒帶trace_id就自己產一個(client):
    resp = await client.get("/health")
    assert len(resp.headers["X-Trace-Id"]) == 36  # UUID v4
