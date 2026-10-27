"""End-to-end UI tests: the built React app + the real backend + HTTP mocks, driven by Playwright.

Run (after `pnpm build` in ../frontend):
    RUN_E2E=1 uv run --group e2e pytest tests/e2e -v
Optional: E2E_SCREENSHOTS=/some/dir to save screenshots, E2E_CHROMIUM_PATH to use a preinstalled Chromium.
"""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

BACKEND = Path(__file__).resolve().parents[2]
DIST = Path(os.environ.get("FRONTEND_DIST", BACKEND.parent / "frontend" / "dist"))
PASSWORD = "e2e-password-123"

if os.environ.get("RUN_E2E") != "1":
    pytest.skip("set RUN_E2E=1 to run the browser tests", allow_module_level=True)

playwright_api = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait(url: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{url} did not come up")


class Stack:
    def __init__(self, base: str, tg: int, bot: int, claude: int) -> None:
        self.base, self.tg, self.bot, self.claude = base, tg, bot, claude

    def add_user(self, key: str, referrer: str) -> None:
        httpx.post(f"http://127.0.0.1:{self.bot}/__test/add_user", json={"key": key, "referrer": referrer})

    def start_bot(self, key: str, code: str) -> None:
        """A new Telegram account opens the bot with ?start=<code>."""
        httpx.post(f"http://127.0.0.1:{self.bot}/__test/start", json={"key": key, "code": code})

    def bot_stores_unknown(self, value: bool = True) -> None:
        httpx.post(f"http://127.0.0.1:{self.bot}/__test/stores_unknown", json={"value": value})

    def _tg(self, path: str, body: dict) -> object:
        return httpx.post(f"http://127.0.0.1:{self.tg}/__test/{path}", json=body).json()["result"]

    def review(self, key: str = "a", ad_id: int | None = None, decision: str = "approve") -> object:
        """Play Telegram's moderators: approve (or decline) ads waiting in review."""
        return self._tg("review", {"key": key, "ad_id": ad_id, "decision": decision})

    def set_ad(self, key: str, ad_id: int, **fields: object) -> None:
        self._tg("set_ad", {"key": key, "ad_id": ad_id, "fields": fields})

    def set_balance(self, key: str, amount: float) -> None:
        self._tg("set_balance", {"key": key, "amount": amount})

    def ignore_edit_formats(self, *formats: str) -> None:
        """editAd answers ok but keeps the old content for these request formats (json, form, query)."""
        self._tg("ignored_formats", {"formats": list(formats)})


@pytest.fixture(scope="session")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    if not (DIST / "index.html").exists():
        pytest.skip(f"frontend is not built ({DIST}); run `pnpm build` in frontend/")
    tg, bot, claude, web = (_free_port() for _ in range(4))
    data = tmp_path_factory.mktemp("e2e-data")
    logs = open(data / "processes.log", "w")  # noqa: SIM115
    mocks = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.mocks.server",
            "--tg-port",
            str(tg),
            "--bot-port",
            str(bot),
            "--claude-port",
            str(claude),
        ],
        cwd=BACKEND,
        stdout=logs,
        stderr=subprocess.STDOUT,
        env={**os.environ, "MOCK_INACTIVE_SECONDS": "1"},
    )
    env = {
        **os.environ,
        "DATA_DIR": str(data),
        "FRONTEND_DIST": str(DIST),
        "ADMIN_PASSWORD": PASSWORD,
        "SESSION_SECRET": "e" * 48,
        "ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "COOKIE_SECURE": "0",
        "TG_ADS_API_BASE": f"http://127.0.0.1:{tg}",
        "ANTHROPIC_API_BASE": f"http://127.0.0.1:{claude}",
        "WITHDRAW_DELAY_SECONDS": "2",
        "MIN_SYNC_SECONDS": "2",
        "LEGACY_TG_ADS_TOKEN": "",
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(web)],
        cwd=BACKEND,
        stdout=logs,
        stderr=subprocess.STDOUT,
        env=env,
    )
    try:
        _wait(f"http://127.0.0.1:{web}/healthz")
        yield Stack(f"http://127.0.0.1:{web}", tg, bot, claude)
    finally:
        for p in (server, mocks):
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        logs.close()


@pytest.fixture(scope="session")
def browser() -> Iterator:
    with playwright_api.sync_playwright() as p:
        exe = os.environ.get("E2E_CHROMIUM_PATH") or None
        b = p.chromium.launch(executable_path=exe)
        yield b
        b.close()


class Shots:
    def __init__(self) -> None:
        d = os.environ.get("E2E_SCREENSHOTS")
        self.dir = Path(d) if d else None
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)

    def __call__(self, page, name: str, full: bool = True) -> None:
        if self.dir:
            page.wait_for_timeout(250)
            page.screenshot(path=str(self.dir / f"{name}.png"), full_page=full)


@pytest.fixture(scope="session")
def shot() -> Shots:
    return Shots()
