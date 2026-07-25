"""設定:全部走環境變數,缺必要變數 fail-fast(平台規約)。"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- 服務識別 ---
    service_name: str = "upload-program"
    environment: str = Field(default="dev", description="dev / staging / production")
    log_level: str = "INFO"

    # 路徑前綴由 Platform 團隊分配,不得自選 —— 因此無預設值,缺少即 fail-fast。
    api_prefix: str

    # --- 資料庫 ---
    database_url: str
    db_pool_size: int = Field(default=10, le=20, description="平台規約:連線池上限 20")
    db_max_overflow: int = Field(default=5)

    # --- OIDC / Keycloak(接入契約 §2) ---
    # 只給 issuer;所有端點一律從 discovery 動態取,不寫死路徑。
    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: str
    oidc_redirect_uri: str
    oidc_scopes: str = "openid profile email"
    jwks_cache_seconds: int = 3600
    clock_skew_seconds: int = 30

    # 登入成功後導回的位置(前端接上後改成前端網址)。
    post_login_redirect: str = "/"
    post_logout_redirect: str = "/"

    # 簽 session cookie 用(非 JWT 簽章金鑰,只保護我們自己的 cookie)。
    session_secret: str
    session_cookie_name: str = "upload_session"
    session_cookie_secure: bool = True

    # 首批平台管理員的 sub(逗號分隔),首登自動 active + admin,解開通的雞生蛋問題。
    bootstrap_admin_subs: str = ""

    # --- 物件儲存(MinIO / S3 相容)---
    s3_endpoint_url: str
    s3_region: str = "us-east-1"
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: str
    # 前端直傳用的對外端點;MinIO 在 compose 內部名稱與對外網址不同時要分開設。
    s3_public_endpoint_url: str = ""
    presign_expire_seconds: int = 900

    # --- 上傳限制 ---
    max_artifact_bytes: int = 512 * 1024 * 1024
    max_project_bytes: int = 5 * 1024 * 1024 * 1024
    # 超過此大小就不在請求週期內重算 SHA-256(標記為未驗證,留給日後背景工作)。
    verify_hash_max_bytes: int = 256 * 1024 * 1024

    # --- 錯誤格式(RFC 7807)---
    problem_type_base: str = "https://platform.sporton.com.tw/errors"

    @field_validator("api_prefix")
    @classmethod
    def _normalise_prefix(cls, v: str) -> str:
        v = "/" + v.strip().strip("/")
        return "" if v == "/" else v

    @field_validator("oidc_issuer")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def bootstrap_admins(self) -> set[str]:
        return {s.strip() for s in self.bootstrap_admin_subs.split(",") if s.strip()}

    @property
    def s3_browser_endpoint(self) -> str:
        return self.s3_public_endpoint_url or self.s3_endpoint_url


@lru_cache
def get_settings() -> Settings:
    """缺少必要環境變數時,這裡會直接拋 ValidationError —— 服務啟動失敗,不用預設值頂替。"""
    return Settings()
