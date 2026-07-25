"""Log:stdout、JSON 單行。禁止輸出密碼 / JWT / 個資(平台規約)。"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
user_id_var: ContextVar[str] = ContextVar("user_id", default="")

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"asctime", "message"}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc)
            .astimezone()
            .isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "service": self.service,
            "message": record.getMessage(),
        }
        if trace_id := trace_id_var.get():
            payload["trace_id"] = trace_id
        if user_id := user_id_var.get():
            payload["user_id"] = user_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(service: str, level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # uvicorn 預設會自帶 handler 印非結構化字串,關掉讓它走 root。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "gunicorn.error", "gunicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    # uvicorn 的 access log 內容與我們的 middleware 重複,且不含 trace_id。
    logging.getLogger("uvicorn.access").disabled = True
