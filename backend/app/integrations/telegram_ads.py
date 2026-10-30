"""Async client for the Telegram Ads API (https://ads.telegram.org/docs/api)."""

import asyncio
import json
import uuid
from decimal import ROUND_DOWN, Decimal
from typing import Any

import httpx

from app.core.config import get_settings

READ_METHODS = {
    "getAccountInfo",
    "getRelatedAccountsList",
    "getTransactionStatus",
    "getCurrentAccount",
    "getAccountsById",
    "getAccountTransactionsList",
    "getAccountStats",
    "getAccountReport",
    "getAdsById",
    "getAdsList",
    "getAdTransactionsList",
    "getAdStats",
    "getAudiencesById",
    "getAudiencesList",
    "getPixel",
    "getPixelEventsById",
    "getPixelEventsList",
    "getTargetLanguagesList",
    "getTargetTopicsList",
    "getTargetCountriesList",
    "getTargetLocationsById",
    "getTargetLocationsList",
    "getTargetChannel",
    "getTargetBot",
}
# The documented ways of passing parameters (multipart is used for file uploads only).
FORMATS = ("json", "form", "query")

ERROR_HINTS = {
    "ACCESS_TOKEN_INVALID": "توکن نامعتبر است یا باطل شده. از Telegram Ads ← API توکن تازه بگیرید.",
    "ACCESS_TOKEN_REQUIRED": "توکن وارد نشده است.",
    "NOT_ENOUGH_BUDGET": "موجودی کافی نیست.",
    "AD_ACTIVE_RECENTLY": "آگهی کمتر از ۱۰ دقیقه پیش فعال بوده؛ بعداً دوباره امتحان می‌شود.",
    "CHANNEL_INVALID": "این کانال پیدا نشد یا برای تبلیغ مجاز نیست.",
    "BOT_INVALID": "این ربات پیدا نشد یا برای تبلیغ مجاز نیست.",
    "CHANNEL_ID_UNKNOWN": "این حساب شناسه‌ی عددی این کانال را نمی‌شناسد؛ کانال را با @username بدهید.",
    "BOT_ID_UNKNOWN": "این حساب شناسه‌ی عددی این ربات را نمی‌شناسد؛ ربات را با @username بدهید.",
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


def flat(v: Any) -> str:
    """A parameter as a string for the query string or a form: lists/objects JSON-serialized (as documented)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list | dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def clip_bytes(text: str, limit: int = 128) -> str:
    """Cut to `limit` UTF-8 bytes without splitting a character (ad titles are "1-128 bytes", not characters)."""
    return text.encode("utf-8")[:limit].decode("utf-8", "ignore")


# Decimal places Telegram keeps for each account currency (docs: "Currencies"). Telegram rounds whatever it is
# sent to these, so we round down first: a rounded-up amount or budget could ask for more than exists.
AMOUNT_PLACES = {"EUR": 5, "TON": 5, "XTR": 3}  # transaction amounts (increase/decrease…Budget)
BUDGET_PLACES = {"EUR": 2, "TON": 2, "XTR": 0}  # cpm, initial_budget, daily_budget_limit


def floor_amount(x: float, places: int = 5) -> float:
    """Round down to `places` decimals so we never ask for more than exists."""
    return float(Decimal(str(x)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN))


def amount_places(currency: str) -> int:
    return AMOUNT_PLACES.get((currency or "").upper(), 5)


def budget_places(currency: str) -> int:
    return BUDGET_PLACES.get((currency or "").upper(), 2)


class TelegramAdsClient:
    def __init__(
        self,
        token: str,
        *,
        base: str | None = None,
        http: httpx.AsyncClient | None = None,
        edit_format: str = "json",
        currency: str = "",
    ) -> None:
        self.token = token
        # The account's currency decides how amounts are rounded (see AMOUNT_PLACES); unknown means TON/EUR.
        self.currency = currency
        # How editAd content changes are sent (see services/bulk.py: probe_edit finds the one Telegram applies).
        self.edit_format = edit_format if edit_format in FORMATS else "json"
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
        fmt: str = "json",
        **params: Any,
    ) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        retries = 3 if (method in READ_METHODS or idempotency_key) else 1
        last_error = ""
        for attempt in range(retries):
            try:
                url = f"{self.base}/{method}"
                if files:
                    data = {k: flat(v) for k, v in params.items()}
                    resp = await self.http.post(url, headers=headers, data=data, files=files)
                elif fmt == "form":
                    resp = await self.http.post(url, headers=headers, data={k: flat(v) for k, v in params.items()})
                elif fmt == "query":
                    resp = await self.http.get(url, headers=headers, params={k: flat(v) for k, v in params.items()})
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

    async def edit_ad(self, ad_id: int, *, fmt: str | None = None, **fields: Any) -> dict[str, Any]:
        return await self.call("editAd", fmt=fmt or self.edit_format, ad_id=ad_id, return_target=True, **fields)

    async def submit_for_review(self, ad_id: int) -> dict[str, Any]:
        """submitAdForReview: Telegram leaves an edited or new ad with an empty budget as "ready_for_review"
        (since September 2026) and does not send it to its moderators until this is called."""
        return await self.call("submitAdForReview", ad_id=ad_id, return_target=True)

    async def withdraw(self, ad_id: int, amount: float, key: str | None = None) -> dict[str, Any]:
        return await self.call(
            "decreaseAdBudget", idempotency_key=key or str(uuid.uuid4()), ad_id=ad_id, amount=self.amount(amount)
        )

    async def topup(self, ad_id: int, amount: float, key: str | None = None) -> dict[str, Any]:
        return await self.call(
            "increaseAdBudget", idempotency_key=key or str(uuid.uuid4()), ad_id=ad_id, amount=self.amount(amount)
        )

    async def upload_photo(self, filename: str, content: bytes, content_type: str) -> dict[str, Any]:
        return await self.call("uploadAdPhoto", files={"file": (filename, content, content_type)})

    def amount(self, x: float) -> float:
        """A transaction amount rounded down to this account's amount precision."""
        return floor_amount(x, amount_places(self.currency))

    def budget(self, x: float) -> float:
        """An initial budget or daily limit rounded down to this account's cpm & budget precision."""
        return floor_amount(x, budget_places(self.currency))

    async def create_ad(self, key: str, **params: Any) -> dict[str, Any]:
        for k in ("initial_budget", "daily_budget_limit"):
            if params.get(k) is not None:
                params[k] = self.budget(float(params[k]))
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


class TelegramAdsApi(TelegramAdsClient):
    """Every method of the Telegram Ads API documentation, with its documented parameter names.

    The panel itself uses only a part (above); the rest is here so the whole documented API is covered by
    tests (tests/test_tgads_api.py). Optional parameters go in **opt with their documented names.
    """

    # accounts
    async def create_account(
        self, title: str, full_name: str, email: str, phone_number: str, country: str, city: str, key: str, **opt: Any
    ) -> dict[str, Any]:
        return await self.call(
            "createAccount",
            idempotency_key=key,
            title=title,
            full_name=full_name,
            email=email,
            phone_number=phone_number,
            country=country,
            city=city,
            **opt,
        )

    async def edit_account_info(self, **opt: Any) -> dict[str, Any]:
        return await self.call("editAccountInfo", **opt)

    async def account_info(self, **opt: Any) -> dict[str, Any]:
        return await self.call("getAccountInfo", **opt)

    async def related_accounts(self, **opt: Any) -> dict[str, Any]:
        return await self.call("getRelatedAccountsList", **opt)

    async def increase_account_budget(self, account_id: str, amount: float, key: str) -> dict[str, Any]:
        return await self.call(
            "increaseAccountBudget", idempotency_key=key, account_id=account_id, amount=self.amount(amount)
        )

    async def decrease_account_budget(self, account_id: str, amount: float, key: str) -> dict[str, Any]:
        return await self.call(
            "decreaseAccountBudget", idempotency_key=key, account_id=account_id, amount=self.amount(amount)
        )

    async def transaction_status(self, transaction_id: str) -> dict[str, Any]:
        return await self.call("getTransactionStatus", transaction_id=transaction_id)

    async def accounts_by_id(self, account_ids: list[str]) -> list[dict[str, Any]]:
        return await self.call("getAccountsById", account_ids=account_ids)

    async def account_transactions(self, **opt: Any) -> dict[str, Any]:
        return await self.call("getAccountTransactionsList", **opt)

    async def account_stats(self, from_time: int, to_time: int, interval: int, **opt: Any) -> list[dict[str, Any]]:
        return await self.call("getAccountStats", from_time=from_time, to_time=to_time, interval=interval, **opt)

    async def account_report(self, year: int, month: int, **opt: Any) -> list[dict[str, Any]]:
        return await self.call("getAccountReport", year=year, month=month, **opt)

    # uploads
    async def upload_video(self, filename: str, content: bytes, content_type: str = "video/mp4") -> dict[str, Any]:
        return await self.call("uploadAdVideo", files={"file": (filename, content, content_type)})

    async def upload_website_photo(self, filename: str, content: bytes, content_type: str) -> dict[str, Any]:
        return await self.call("uploadWebsitePhoto", files={"file": (filename, content, content_type)})

    # ads
    async def edit_ad_status(self, ad_id: int, is_paused: bool) -> dict[str, Any]:
        """Deprecated by Telegram; editAd(is_paused=...) is what the panel uses."""
        return await self.call("editAdStatus", ad_id=ad_id, is_paused=is_paused)

    async def ad_transactions(self, ad_id: int, **opt: Any) -> dict[str, Any]:
        return await self.call("getAdTransactionsList", ad_id=ad_id, **opt)

    async def ad_stats(self, ad_id: int, from_time: int, to_time: int, interval: int) -> list[dict[str, Any]]:
        return await self.call("getAdStats", ad_id=ad_id, from_time=from_time, to_time=to_time, interval=interval)

    async def ads_page(self, **opt: Any) -> dict[str, Any]:
        return await self.call("getAdsList", **opt)

    # audiences
    async def create_audience(self, title: str, key: str, **opt: Any) -> dict[str, Any]:
        return await self.call("createAudience", idempotency_key=key, title=title, **opt)

    async def edit_audience(self, audience_id: int, **opt: Any) -> dict[str, Any]:
        return await self.call("editAudience", audience_id=audience_id, **opt)

    async def delete_audience(self, audience_id: int) -> Any:
        return await self.call("deleteAudience", audience_id=audience_id)

    async def audiences_by_id(self, audience_ids: list[int]) -> list[dict[str, Any]]:
        return await self.call("getAudiencesById", audience_ids=audience_ids)

    async def audiences(self, **opt: Any) -> dict[str, Any]:
        return await self.call("getAudiencesList", **opt)

    # pixel
    async def create_pixel(self, key: str) -> dict[str, Any]:
        return await self.call("createPixel", idempotency_key=key)

    async def pixel(self) -> dict[str, Any]:
        return await self.call("getPixel")

    async def create_pixel_event(self, title: str, type: str, key: str) -> dict[str, Any]:
        return await self.call("createPixelEvent", idempotency_key=key, title=title, type=type)

    async def edit_pixel_event(self, event_id: str, title: str) -> dict[str, Any]:
        return await self.call("editPixelEvent", event_id=event_id, title=title)

    async def delete_pixel_event(self, event_id: str) -> Any:
        return await self.call("deletePixelEvent", event_id=event_id)

    async def pixel_events_by_id(self, event_ids: list[str]) -> list[dict[str, Any]]:
        return await self.call("getPixelEventsById", event_ids=event_ids)

    async def pixel_events(self, **opt: Any) -> dict[str, Any]:
        return await self.call("getPixelEventsList", **opt)

    # targets
    async def target_languages(self) -> list[dict[str, Any]]:
        return await self.call("getTargetLanguagesList")

    async def target_topics(self) -> list[dict[str, Any]]:
        return await self.call("getTargetTopicsList")

    async def target_countries(self) -> list[dict[str, Any]]:
        return await self.call("getTargetCountriesList")

    async def target_locations_by_id(self, location_ids: list[int]) -> list[dict[str, Any]]:
        return await self.call("getTargetLocationsById", location_ids=location_ids)

    async def target_locations(self, country_code: str, query: str, **opt: Any) -> dict[str, Any]:
        return await self.call("getTargetLocationsList", country_code=country_code, query=query, **opt)
