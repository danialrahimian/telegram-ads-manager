"""Async client for the Telegram Ads API (https://ads.telegram.org/docs/api)."""

import asyncio
import json
import uuid
from decimal import ROUND_DOWN, Decimal
from typing import Any

import httpx

from app.core.config import get_settings

READ_METHODS = {
    "getCurrentAccount",
    "getAdsList",
    "getAdsById",
    "getAdStats",
    "getTargetChannel",
    "getTargetBot",
    "getAccountStats",
    "getAdTransactionsList",
}

ERROR_HINTS = {
    "ACCESS_TOKEN_INVALID": "توکن نامعتبر است یا باطل شده. از Telegram Ads ← API توکن تازه بگیرید.",
    "ACCESS_TOKEN_REQUIRED": "توکن وارد نشده است.",
    "NOT_ENOUGH_BUDGET": "موجودی کافی نیست.",
    "AD_ACTIVE_RECENTLY": "آگهی کمتر از ۱۰ دقیقه پیش فعال بوده؛ بعداً دوباره امتحان می‌شود.",
    "CHANNEL_INVALID": "این کانال پیدا نشد یا برای تبلیغ مجاز نیست.",
    "BOT_INVALID": "این ربات پیدا نشد یا برای تبلیغ مجاز نیست.",
    "AD_TEXT_TOO_LONG": "متن آگهی بیشتر از ۱۶۰ کاراکتر است.",
}


class TelegramAdsError(Exception):
    def __init__(self, method: str, error: str, http_status: int | None = None) -> None:
        super().__init__(f"{method}: {error}")
        self.method = method
        self.error = error
        self.http_status = http_status

    @property
    def friendly(self) -> str:
        if self.error in ERROR_HINTS:
            return ERROR_HINTS[self.error]
        up = self.error.upper()
        if "BUDGET" in up and any(x in up for x in ("LOW", "MIN", "SMALL", "TOO_FEW", "INVALID")):
            return (
                "تلگرام بودجه‌ی اولیه را کمتر از حداقل مجاز دانست (حداقل ۱ TON). "
                "بودجه را بیشتر کنید یا «حداقل بودجه‌ی اولیه» را در تنظیمات حساب درست کنید."
            )
        if "IP" in self.error.upper():
            return f"تلگرام درخواست را از این IP نپذیرفت ({self.error}). IP سرور را در Telegram Ads ← API ثبت کنید."
        if self.error.startswith("network"):
            return "اتصال به Telegram Ads برقرار نشد. اینترنت سرور را بررسی کنید."
        return self.error


def floor_amount(x: float, places: str = "0.00001") -> float:
    """Round down to the API's amount precision so we never ask for more than exists."""
    return float(Decimal(str(x)).quantize(Decimal(places), rounding=ROUND_DOWN))


class TelegramAdsClient:
    def __init__(self, token: str, *, base: str | None = None, http: httpx.AsyncClient | None = None) -> None:
        self.token = token
        self.base = (base or get_settings().telegram_ads_api_base).rstrip("/")
        self._http = http
        self._own_http = http is None

    async def __aenter__(self) -> "TelegramAdsClient":
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10))
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._own_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10))
        return self._http

    async def call(
        self,
        method: str,
        *,
        idempotency_key: str | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        **params: Any,
    ) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        retries = 3 if (method in READ_METHODS or idempotency_key) else 1
        last_error = ""
        for attempt in range(retries):
            try:
                if files:
                    data = {
                        k: json.dumps(v) if isinstance(v, list | dict | bool) else str(v) for k, v in params.items()
                    }
                    resp = await self.http.post(f"{self.base}/{method}", headers=headers, data=data, files=files)
                else:
                    # Always send a JSON body: Telegram answers HTTP 411 to a body-less POST.
                    resp = await self.http.post(f"{self.base}/{method}", headers=headers, json=params)
            except httpx.HTTPError as e:
                last_error = f"network error: {e.__class__.__name__}"
                if attempt + 1 < retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                raise TelegramAdsError(method, last_error) from e
            if resp.status_code == 429 and attempt + 1 < retries:
                await asyncio.sleep(min(float(resp.headers.get("Retry-After", "3")), 30))
                continue
            try:
                body = resp.json()
            except ValueError:
                raise TelegramAdsError(method, f"HTTP {resp.status_code}", resp.status_code) from None
            if body.get("ok"):
                return body.get("result")
            err = str(body.get("error") or f"HTTP {resp.status_code}")
            if err == "IDEMPOTENT_REQUEST_IN_PROGRESS" and attempt + 1 < retries:
                await asyncio.sleep(2)
                continue
            raise TelegramAdsError(method, err, resp.status_code)
        raise TelegramAdsError(method, last_error or "unknown error")

    # ---- reads ----
    async def account(self) -> dict[str, Any]:
        return await self.call("getCurrentAccount")

    async def ads(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset: str | None = None
        for _ in range(200):  # hard stop: 20,000 ads
            params: dict[str, Any] = {"limit": 100, "return_target": True}
            if offset:
                params["offset"] = offset
            page = await self.call("getAdsList", **params)
            out.extend(page.get("ads", []))
            offset = page.get("next_offset")
            if not offset:
                break
        return out

    async def ads_by_id(self, ids: list[int]) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for i in range(0, len(ids), 100):
            for ad in await self.call("getAdsById", ad_ids=ids[i : i + 100]):
                out[int(ad["ad_id"])] = ad
        return out

    async def ad_stats_daily(self, ad_id: int, from_time: int, to_time: int) -> list[dict[str, Any]]:
        return await self.call("getAdStats", ad_id=ad_id, from_time=from_time, to_time=to_time, interval=86400)

    async def target_channel(self, username: str) -> dict[str, Any]:
        return await self.call("getTargetChannel", channel_id=username)

    async def target_bot(self, username: str) -> dict[str, Any]:
        return await self.call("getTargetBot", bot_id=username)

    # ---- writes (each carries an idempotency key so a retry can never double-spend) ----
    async def set_paused(self, ad_id: int, paused: bool) -> dict[str, Any]:
        return await self.call("editAd", ad_id=ad_id, is_paused=paused)

    async def edit_ad(self, ad_id: int, **fields: Any) -> dict[str, Any]:
        return await self.call("editAd", ad_id=ad_id, return_target=True, **fields)

    async def withdraw(self, ad_id: int, amount: float, key: str | None = None) -> dict[str, Any]:
        return await self.call(
            "decreaseAdBudget", idempotency_key=key or str(uuid.uuid4()), ad_id=ad_id, amount=floor_amount(amount)
        )

    async def topup(self, ad_id: int, amount: float, key: str | None = None) -> dict[str, Any]:
        return await self.call(
            "increaseAdBudget", idempotency_key=key or str(uuid.uuid4()), ad_id=ad_id, amount=floor_amount(amount)
        )

    async def upload_photo(self, filename: str, content: bytes, content_type: str) -> dict[str, Any]:
        return await self.call("uploadAdPhoto", files={"file": (filename, content, content_type)})

    async def create_ad(self, key: str, **params: Any) -> dict[str, Any]:
        return await self.call("createAd", idempotency_key=key, **params)

    async def delete_ad(self, ad_id: int) -> Any:
        """Only called after probe_capabilities() found the method (see services/bulk.py)."""
        return await self.call("deleteAd", ad_id=ad_id)

    async def probe_capabilities(self) -> dict[str, str]:
        """Find out, without touching any ad, whether deleting ads and changing a live ad's photo exist.

        The public documentation is behind a login, so nothing is assumed:
          * deleteAd is called with an empty body. No ad id means nothing can be deleted; an unknown
            method answers differently from a known method that misses its parameter.
          * editAd is called for ad 0 (never a real ad) with an invalid photo id. A photo error shows the
            parameter is understood; any other answer leaves it "unknown" (a real one-ad test settles it).
        Values: "yes" | "no" | "unknown".
        """
        out = {"delete_ad": "unknown", "edit_photo": "unknown"}
        try:
            await self.call("deleteAd")
            out["delete_ad"] = "yes"
        except TelegramAdsError as e:
            out["delete_ad"] = classify_probe(e)
        try:
            await self.call("editAd", ad_id=0, photo_id="probe-invalid")
            out["edit_photo"] = "unknown"
        except TelegramAdsError as e:
            err = e.error.upper()
            out["edit_photo"] = "yes" if "PHOTO" in err else "unknown"
        return out


def classify_probe(e: TelegramAdsError) -> str:
    err = e.error.upper()
    if e.http_status == 404 or "METHOD" in err:
        return "no"
    if err.startswith("AD_") or err.endswith("_REQUIRED") or err.endswith("_INVALID") or "PARAM" in err:
        return "yes"
    if "NOT_FOUND" in err or "UNKNOWN" in err:
        return "no"
    return "unknown"
