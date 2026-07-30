"""測試共用夾具。

🔴 契約 §4.8:測試一律假帳號,嚴禁真實個資。本檔所有 sub / token 都是編出來的字串。
"""

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.db import Base
from app.main import create_app
from app.models import User, UserStatus
from app.storage import TooLarge

TEST_ISSUER = "https://auth.example.test/realms/test"
TEST_CLIENT_ID = "upload-program-test"


class FakeOidc:
    """假 IdP:測試不打真實 Keycloak,但保留「token 無效就 401」的行為。

    🐛 T52 補上**過期**與 **refresh**:原本這個替身的 token 永不過期,
    於是「access token 只有 300 秒、網頁零 JS 不會續期」這個缺陷藏了很久
    ——240 條測試一條都抓不到。**替身與真實行為的落差本身就是風險。**
    """

    def __init__(self) -> None:
        self.tokens: dict[str, dict] = {}
        self.expired: set[str] = set()
        self.refresh_tokens: dict[str, str] = {}  # refresh_token -> sub
        self.dead_refresh: set[str] = set()  # IdP 端已失效(例:帳號被停用)
        self.refresh_calls = 0
        self.ready = True

    def issue(self, sub: str, *, expired: bool = False, **extra) -> str:
        token = f"tok-{uuid.uuid4()}"
        self.tokens[token] = {"sub": sub, "aud": TEST_CLIENT_ID, "iss": TEST_ISSUER, **extra}
        if expired:
            self.expired.add(token)
        return token

    def issue_refresh(self, sub: str) -> str:
        rt = f"rt-{uuid.uuid4()}"
        self.refresh_tokens[rt] = sub
        return rt

    async def load_discovery(self, force: bool = False):
        return None

    def authorization_url(self, discovery, state, challenge, nonce, prompt=None):
        # T64:記下 prompt 供測試斷言;回傳假的 IdP 授權網址
        self.last_prompt = prompt
        return f"https://idp.example.test/auth?state={state}&prompt={prompt or ''}"

    def verify(self, token: str, *, expected_nonce: str | None = None) -> dict:
        return self.verify_access_token(token)

    def verify_access_token(self, token: str) -> dict:
        from app.problems import unauthorized

        if token in self.expired:
            raise unauthorized("token 已過期(測試用假 IdP)")
        claims = self.tokens.get(token)
        if claims is None:
            raise unauthorized("token 無效(測試用假 IdP)")
        return claims

    async def refresh(self, refresh_token: str) -> dict:
        """換一組**新的、未過期的** token;refresh 本身失效時拋 401(與真實一致)。"""
        from app.problems import unauthorized

        self.refresh_calls += 1
        sub = self.refresh_tokens.get(refresh_token)
        if sub is None or refresh_token in self.dead_refresh:
            raise unauthorized("refresh token 已失效(測試用假 IdP)")
        return {
            "access_token": self.issue(sub),
            "refresh_token": self.issue_refresh(sub),
            "id_token": "fake-id-token",
        }


class FakeStorage:
    """記憶體版物件儲存,行為對齊 ObjectStorage(含 magic bytes 前置檢查與大小上限)。"""

    def __init__(self, sniff_bytes: int = 4096) -> None:
        self.objects: dict[str, bytes] = {}
        self._sniff = sniff_bytes

    async def ensure_bucket(self) -> None: ...

    async def check_ready(self) -> None: ...

    async def upload_stream(self, key, chunks, max_bytes, on_head=None):
        import hashlib

        from app.storage import UploadResult

        body = bytearray()
        async for chunk in chunks:
            body.extend(chunk)
            if len(body) > max_bytes:
                raise TooLarge(max_bytes)
        head = bytes(body[: self._sniff])
        if on_head is not None:
            on_head(head)  # 判型不過就中止,且不留下物件
        self.objects[key] = bytes(body)
        return UploadResult(
            size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), head=head
        )

    async def iter_object(self, key: str) -> AsyncIterator[bytes]:
        yield self.objects[key]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def delete_prefix(self, prefix: str) -> None:
        for key in [k for k in self.objects if k.startswith(prefix)]:
            del self.objects[key]


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        public_base_url="https://catsapp.example.test",
        api_prefix="/upload",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        oidc_issuer=TEST_ISSUER,
        oidc_client_id=TEST_CLIENT_ID,
        oidc_client_secret="not-a-real-secret",
        session_secret="not-a-real-session-secret",
        s3_endpoint_url="http://minio.invalid:9000",
        s3_bucket="test-bucket",
        s3_access_key="test",
        s3_secret_key="test",
        session_cookie_secure=False,
        max_artifact_bytes=1024 * 1024,
        # 測試用小數字才跑得快;真實級距數值由 test_config_and_logging 的預設值測試把關。
        max_project_bytes=4 * 1024 * 1024,
        max_project_extended_bytes=16 * 1024 * 1024,
    )


@pytest.fixture
async def app(settings):
    application = create_app(settings)
    application.state.oidc = FakeOidc()
    application.state.storage = FakeStorage(settings.magic_sniff_bytes)

    async with application.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield application
    await application.state.engine.dispose()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # T64:預設帶探測 cookie=模擬「已靜默探測過」的瀏覽器——既有測試斷言的
        # 是落地頁本身的內容與行為,不是首訪的探測轉址;首訪行為由
        # test_silent_sso.py 自行清掉這個 cookie 來驗。
        ac.cookies.set(app.state.cookies.sso_probe_cookie_name, "1")
        yield ac


@pytest.fixture
def oidc(app) -> FakeOidc:
    return app.state.oidc


@pytest.fixture
def storage(app) -> FakeStorage:
    return app.state.storage


async def make_user(app, sub: str, status: UserStatus = UserStatus.active, admin: bool = False):
    """直接在 DB 造一個已開通(或指定狀態)的假帳號。"""
    from app.models import PlatformRole

    async with app.state.sessionmaker() as session:
        user = User(
            sub=sub,
            status=status,
            platform_role=PlatformRole.admin if admin else PlatformRole.member,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def active_user(app, oidc):
    user = await make_user(app, "sub-active")
    return user, oidc.issue("sub-active")


@pytest.fixture
async def admin_user(app, oidc):
    user = await make_user(app, "sub-admin", admin=True)
    return user, oidc.issue("sub-admin")
