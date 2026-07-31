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

    # T60(施工單 v1.3 §4.4):伺服器端呼叫的**內部位址覆寫**。
    # 容器內連對外網址不通(本專案上線當天實測),而 discovery 文件裡的端點
    # 全是對外網址——伺服器發出的請求(抓 discovery、code 換 token、取 JWKS)
    # 改走這三個值;留空 = 照舊從 issuer/discovery 推導(向後相容,VM 未改
    # .env 前維持 hairpin 形態不會壞)。
    # 🔴 OIDC_ISSUER 與瀏覽器端點(authorization/end_session)永遠維持對外:
    #    iss 是字串比對不是連線;瀏覽器連不到內部位址。
    oidc_discovery_url: str = ""
    oidc_token_url: str = ""
    oidc_jwks_url: str = ""

    post_login_path: str = "/"
    post_logout_path: str = "/"

    # 契約 §2.1 的 Account Console 短網址(改密碼/個資)。§4.8 禁止 App 自建這些頁面,
    # 只需在 UI 放連結指向它。**刻意不帶本服務的前綴**——它是平台層的 302 轉址,
    # 加上前綴會變成一條不存在的路徑。設定化的理由與「路徑前綴不自選」相同:
    # 平台的東西由平台決定。
    account_console_url: str = "/account"

    # T67:平台入口(catsapp 首頁)。使用者從 portal 卡片進到 `/upload/` 之後沒有回頭路,
    # 側欄與導航列各放一條連結指回去。**同樣刻意不帶本服務前綴**——加上前綴會變成
    # `/upload/` 自己(原地打轉),與 `account_console_url` 是同一類具名例外。
    portal_home_url: str = "/"

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
    # 單檔 100 MB(Q7 裁示「50~100M」取上限)。
    # 專案容量分兩級距(F17/T49):`max_project_bytes` 是 **standard**(預設,2 GB),
    # `max_project_extended_bytes` 是 **extended**(需平台管理員核可,10 GB)。
    # 專案列上只存級距代號,所以調整下面兩個數字不必動任何一列資料。
    # 🔴 調降這兩個值會讓已超標的既有專案立刻無法上傳新檔(既有檔案不受影響)——
    # 上限調高比調低容易,所以先收緊、個案再放寬。
    # 命名注意:結尾必須是 `_BYTES`,防漂移測試靠 `MAX_\w+_BYTES` 這個樣式自動納入防護。
    max_artifact_bytes: int = 100 * 1024 * 1024
    max_project_bytes: int = 2 * 1024 * 1024 * 1024
    max_project_extended_bytes: int = 10 * 1024 * 1024 * 1024
    magic_sniff_bytes: int = 4096

    # --- 稽核紀錄(F54 / T38)---
    # 🔴 保留期是 T37 承諾的一部分:當時刻意不做下載事件表,理由是「誰下載了什麼」
    # 屬於稽核,而稽核「有自己的保存期限與存取權限」。沒有保留期的話,那就只是
    # 把一張無限長大的個資表推給未來。
    # ⚠️ 本服務**沒有排程器**,這個值不會自己生效——由 `tools/purge_audit.py`
    # 搭配 VM 的 cron(或人工)執行。這一點在 dev-log 列為遺留問題,不假裝已自動化。
    audit_retention_days: int = Field(default=365, ge=1)

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
