"""Test setup: isolated data dir, fresh settings, the app with its lifespan, and HTTP mocks via respx."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from cryptography.fernet import Fernet

from tests.mocks.bot_mock import RAW, BotMock
from tests.mocks.tgads_mock import TgAdsMock

TG_BASE = "https://tgads.test"
BOT_BASE = "https://bot.test"
PASSWORD = "test-password-123"


@pytest.fixture
def env(tmp_path, monkeypatch) -> dict[str, str]:
    values = {
        "DATA_DIR": str(tmp_path / "data"),
        "FRONTEND_DIST": str(tmp_path / "dist"),
        "ADMIN_PASSWORD": PASSWORD,
        "SESSION_SECRET": "s" * 48,
        "ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "COOKIE_SECURE": "0",
        "SCHEDULER_ENABLED": "0",
        "WITHDRAW_DELAY_SECONDS": "0",
        "MIN_SYNC_SECONDS": "1",
        "TG_ADS_API_BASE": TG_BASE,
        "ANTHROPIC_API_BASE": "https://claude.test",
        "LEGACY_TG_ADS_TOKEN": "",
    }
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    from app.core import crypto
    from app.core.config import get_settings

    get_settings.cache_clear()
    crypto._fernet.cache_clear()
    yield values
    get_settings.cache_clear()
    crypto._fernet.cache_clear()


@pytest.fixture
def tg_mock() -> TgAdsMock:
    return TgAdsMock(inactive_seconds=0)


@pytest.fixture
def bot_mock() -> BotMock:
    return BotMock()


@pytest.fixture
def http_mocks(tg_mock: TgAdsMock, bot_mock: BotMock) -> AsyncIterator[respx.MockRouter]:
    def tg_side_effect(request: httpx.Request) -> httpx.Response:
        from tests.mocks.tgads_api import parse_request

        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        params, files, fmt = parse_request(
            request.method, request.url.query.decode(), request.headers.get("Content-Type", ""), request.content
        )
        idem = {"Idempotency-Key": request.headers["Idempotency-Key"]} if "Idempotency-Key" in request.headers else {}
        body, headers = tg_mock.respond(token, request.url.path, params, files, idem, fmt)
        return httpx.Response(200, json=body, headers=headers)

    def bot_side_effect(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        code, res = bot_mock.handle(request.headers.get("Token", ""), request.method, request.url.path, body)
        if RAW in res:
            return httpx.Response(code, text=res[RAW], headers={"Content-Type": "text/html"})
        return httpx.Response(code, json=res)

    def claude_side_effect(request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-api-key") != "sk-ant-test":
            return httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})
        body = json.loads(request.content)
        user = body["messages"][0]["content"]
        if "بازنویسی منطبق با قوانین" in user:
            from tests.mocks.server import claude_reply

            return httpx.Response(200, json={"content": [{"type": "text", "text": claude_reply(user)}]})
        if "سه بازنویسی" in user:
            text = json.dumps(
                {
                    "score": 62,
                    "summary": "پیشنهاد روشن ولی بدون نام برند.",
                    "strengths": ["قیمت روشن"],
                    "issues": [{"issue": "نام برند نیست", "severity": "high", "fix": "نام برند را اول بیاورید"}],
                    "rewrites": [
                        {"text": "سایکونت | تست رایگان VPN 👇", "angle": "تست رایگان"},
                        {"text": "x" * 170, "angle": "خیلی بلند"},
                        {"text": "وصل بمون با سایکونت 👇", "angle": "پایداری"},
                    ],
                },
                ensure_ascii=False,
            )
            return httpx.Response(200, json={"content": [{"type": "text", "text": "```json\n" + text + "\n```"}]})
        return httpx.Response(200, json={"content": [{"type": "text", "text": "## تحلیل\nهمه‌چیز خوب است. OK"}]})

    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        router.route(url__startswith=TG_BASE).mock(side_effect=tg_side_effect)
        router.route(url__startswith=BOT_BASE).mock(side_effect=bot_side_effect)
        router.route(url__startswith="https://claude.test").mock(side_effect=claude_side_effect)
        router.route(url__startswith="https://api.ipify.org").mock(return_value=httpx.Response(200, text="203.0.113.7"))
        router.route(url__startswith="https://ifconfig.me").mock(return_value=httpx.Response(200, text="203.0.113.7"))
        yield router


@pytest.fixture
async def app(env, http_mocks):
    from app.db.session import dispose_engine
    from app.main import create_app
    from app.workers.scheduler import scheduler

    application = create_app()
    async with application.router.lifespan_context(application):
        yield application
    await scheduler.stop()
    await dispose_engine()


class Api:
    """Thin test client that keeps the session cookie and sends the CSRF header like the SPA does."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.c = client

    def _headers(self) -> dict[str, str]:
        tok = self.c.cookies.get("tgads_csrf")
        return {"X-CSRF-Token": tok} if tok else {}

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.c.get(url, **kw)

    async def post(self, url: str, **kw: Any) -> httpx.Response:
        return await self.c.post(url, headers=self._headers(), **kw)

    async def put(self, url: str, **kw: Any) -> httpx.Response:
        return await self.c.put(url, headers=self._headers(), **kw)

    async def patch(self, url: str, **kw: Any) -> httpx.Response:
        return await self.c.patch(url, headers=self._headers(), **kw)

    async def delete(self, url: str, **kw: Any) -> httpx.Response:
        return await self.c.delete(url, headers=self._headers(), **kw)

    async def login(self, password: str = PASSWORD) -> httpx.Response:
        await self.get("/api/v1/auth/me")
        return await self.c.post("/api/v1/auth/login", json={"password": password})


@pytest.fixture
async def anon(app) -> AsyncIterator[Api]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield Api(c)


@pytest.fixture
async def api(anon: Api) -> Api:
    r = await anon.login()
    assert r.status_code == 200, r.text
    return anon


async def make_account(
    api: Api,
    slug: str = "psychonet",
    name: str = "سایکونت",
    token: str = "mocktoken-a",
    bot_key: str | None = "botkey-a",
    bot_username: str = "psychonet_test_bot",
) -> dict:
    r = await api.post("/api/v1/accounts", json={"name": name, "slug": slug})
    assert r.status_code == 201, r.text
    r = await api.put(f"/api/v1/accounts/{slug}/telegram", json={"token": token})
    assert r.status_code == 200, r.text
    if bot_key:
        r = await api.put(
            f"/api/v1/accounts/{slug}/bot",
            json={"base_url": BOT_BASE + "/api/docs", "username": bot_username, "api_key": bot_key},
        )
        assert r.status_code == 200, r.text
    return r.json()
