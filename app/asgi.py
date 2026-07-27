"""正式部署的 ASGI 進入點:`gunicorn app.asgi:app`。

為什麼獨立成一個模組:`create_app()` 會讀環境變數,若把它寫在 `app/main.py` 的模組層,
**光是 import `app.main` 就會要求完整的 .env**——測試、alembic、任何想借用工廠函式的工具
都會因此 ImportError。把副作用集中在這裡,`app.main` 就維持成可安全 import 的純模組。
"""

from .main import create_app

app = create_app()
