"""設定:全部走環境變數,缺必要變數 fail-fast(平台規約)。

⚠️ 子路徑部署:本服務掛在 gateway 的 `/«PREFIX»/` 底下,且 gateway 以尾斜線
`proxy_pass http://<alias>:8080/;` **剝掉前綴**後轉發。因此:

- 路由一律註冊在**根路徑**(`/v1/...`、`/health`),不要自己再加前綴;
- 對外絕對網址(OIDC redirect、下載連結)一律用 `public_base_url + api_prefix` 組出來,
  不從 request 推導(TLS 在 gateway 終結,推導容易變成 http://)。
"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- 服務識別 ---
    service_name: str = "upload-program"
    environment: str = Field(default="dev", description="dev / staging / production")
    log_level: str = "INFO"

    # 對外主機(Cats 平台單一入口),例:https://catsapp.sporton.com.tw
    public_base_url: str
    # 路徑前綴由平台方分配,不得自選 —— 無預設值,缺少即 fail-fast。
    api_prefix: str

    # --- 資料庫 ---
    database_url: str
    db_pool_size: int = Field(default=10, le=20, description="平台規約:連線池上限 20")
    db_max_overflow: int = Field(default=5)

    # --- OIDC / Keycloak(接入契約 §2)---
    # 只給 issuer;所有端點一律從 discovery 動態取,不寫死路徑。
    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: str
    # 留空則自動組成 <public_base_url><api_prefix>/oidc/callback/
    oidc_redirect_uri_override: str = ""
    oidc_scopes: str = "openid profile email"
    jwks_cache_seconds: int = 3600
    clock_skew_seconds: int = 30
    oidc_http_timeout_seconds: float = 5.0

    post_login_path: str = "/"
    post_logout_path: str = "/"

    # 簽我們自己的 session cookie 用(不是 JWT 簽章金鑰)。
    session_secret: str
    session_cookie_name: str = "upload_session"
    session_cookie_secure: bool = True
    session_max_age_seconds: int = 10 * 3600

    # 首批平台管理員的 sub(逗號分隔),首登自動 active + admin,解開通的雞生蛋問題。
    bootstrap_admin_subs: str = ""

    # --- 物件儲存(MinIO,只在 backend 網路內可達)---
    # 🔴 瀏覽器不直連物件儲存:全 VM 只有 portal-gateway 持 80/443,MinIO 不上 cats-edge。
    #    所以上傳/下載一律由本服務串流代收代送。
    s3_endpoint_url: str
    s3_region: str = "us-east-1"
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: str
    s3_multipart_chunk_bytes: int = 16 * 1024 * 1024

    # --- 上傳限制(gateway 的 client_max_body_size 要 ≥ 這個值,申請路由時一併提出)---
    # 單檔 100 MB(Q7 裁示「50~100M」取上限);專案 2 GB(Q10 裁示,一般小工具夠用)。
    # 🔴 調降此值會讓已超標的既有專案立刻無法上傳新檔(既有檔案不受影響)——
    # 上限調高比調低容易,所以先收緊,個案走 T49 的擴充級距(10 GB)。
    max_artifact_bytes: int = 100 * 1024 * 1024
    max_project_bytes: int = 2 * 1024 * 1024 * 1024
    magic_sniff_bytes: int = 4096

    # --- 錯誤格式(RFC 7807)---
    problem_type_base: str = "https://catsapp.sporton.com.tw/errors"

    @field_validator("api_prefix")
    @classmethod
    def _normalise_prefix(cls, v: str) -> str:
        v = "/" + v.strip().strip("/")
        return "" if v == "/" else v

    @field_validator("public_base_url", "oidc_issuer", "s3_endpoint_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    # --- 衍生值 ---

    @property
    def external_base(self) -> str:
        """本服務對外的根,例:https://catsapp.sporton.com.tw/upload"""
        return f"{self.public_base_url}{self.api_prefix}"

    @property
    def oidc_redirect_uri(self) -> str:
        return self.oidc_redirect_uri_override or f"{self.external_base}/oidc/callback/"

    @property
    def cookie_path(self) -> str:
        """cookie 綁到自己的前綴,避免與同主機其他 App 的 cookie 互蓋(接入指南 §6)。"""
        return f"{self.api_prefix}/" if self.api_prefix else "/"

    @property
    def bootstrap_admins(self) -> set[str]:
        return {s.strip() for s in self.bootstrap_admin_subs.split(",") if s.strip()}


@lru_cache
def get_settings() -> Settings:
    """缺少必要環境變數時,這裡會直接拋 ValidationError —— 服務啟動失敗,不用預設值頂替。"""
    return Settings()
