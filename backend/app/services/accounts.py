"""Accounts: create/edit, connections (Telegram Ads token, sales bot API key) and health checks."""

import contextlib
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import get_settings
from app.core.errors import AppError, Conflict, NotFound
from app.db.base import now_ts
from app.integrations.bot_api import (
    BotApiClient,
    BotApiError,
    extract_rows,
    field_names,
    normalize_base_url,
    retryable,
)
from app.integrations.telegram_ads import TelegramAdsClient, TelegramAdsError
from app.models import Account
from app.services import events

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{4,32}$")
COLORS = ("teal", "violet", "amber", "rose", "sky", "lime")

DEFAULT_SETTINGS: dict[str, Any] = {
    # Sync cadence (seconds). The fast sync reads the account and every ad; stats fetch daily rows per ad;
    # the bot sync reads the sales bot (stats, transactions, referrals).
    "sync_fast_seconds": 30,
    "sync_stats_seconds": 600,
    "sync_bot_seconds": 120,
    # Goals and rules (amounts in the account currency).
    "target_cost_per_customer": 0.08,
    "stop_cost_per_start": 0.045,
    "scale_cost_per_start": 0.025,
    "min_spend_to_judge": 0.5,
    "min_purchases_to_judge": 3,
    "stalled_spend_28d": 0.025,
    "stalled_min_remaining": 0.1,
    "stalled_min_age_days": 7,
    "topup_max_per_ad": 1.0,
    "topup_daily_cap": 5.0,
    "fatigue_age_days": 21,
    "fatigue_ratio": 1.5,
    "refill_max_empty_days": 45,  # a good ad without budget for longer than this is not proposed for a refill
    "replace_zero_view_days": 5,  # days with budget and no views (while other ads are shown) -> replacement
    # Bulk operations: how many ads go to Telegram per batch, how often, and how many may wait in review.
    "bulk_batch_size": 5,
    "bulk_batch_interval_seconds": 120,
    "bulk_max_in_review": 10,
    # Telegram refuses to create an ad with less initial budget than this (1 TON at the time of writing).
    "min_initial_budget": 1.0,
    # Initial budgets of new ads made by bulk replacements per 24 h (separate from the top-up cap).
    "bulk_daily_new_budget_cap": 20.0,
    # New ads
    "default_cpm_channel": 0.45,
    "default_cpm_bot": 0.5,
    "default_budget": 1.0,
    # Sales bot
    "bot_timezone": "Asia/Tehran",
    "purchase_types": ["buy", "buy_extend"],
    "toman_per_unit": 0,  # price of 1 TON/EUR/Star in Toman, for return on ad spend; 0 = not set
    # Referrer accounts owned by the advertiser, used for per-ad referral links.
    "referrer_pool": [],
    # Result of the "random code tracking" test: does the bot record users who start it with an unknown
    # numeric code? works | ignored | unknown (set by services/tracking.py, not edited by hand).
    "random_tracking": "unknown",
}

INTERVAL_KEYS = {"sync_fast_seconds": 10, "sync_stats_seconds": 120, "sync_bot_seconds": 30}
NUMERIC_KEYS = {k for k, v in DEFAULT_SETTINGS.items() if isinstance(v, int | float) and not isinstance(v, bool)}


def settings_of(acc: Account) -> dict[str, Any]:
    merged = dict(DEFAULT_SETTINGS)
    merged.update(acc.settings or {})
    return merged


def validate_settings_patch(patch: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    floor = get_settings().min_sync_seconds
    for k, v in patch.items():
        if k not in DEFAULT_SETTINGS:
            raise AppError(f"تنظیم ناشناخته: {k}")
        if k in NUMERIC_KEYS:
            try:
                num = float(v)
            except (TypeError, ValueError):
                raise AppError(f"مقدار {k} باید عدد باشد.") from None
            if num < 0:
                raise AppError(f"مقدار {k} نمی‌تواند منفی باشد.")
            if k == "min_initial_budget" and num <= 0:
                raise AppError("حداقل بودجه‌ی اولیه باید بیشتر از صفر باشد.")
            if k in INTERVAL_KEYS:
                # MIN_SYNC_SECONDS below 10 is a test setting that lowers every floor.
                num = max(int(num), INTERVAL_KEYS[k] if floor >= 10 else floor)
            out[k] = int(num) if isinstance(DEFAULT_SETTINGS[k], int) else num
        elif k == "purchase_types":
            if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
                raise AppError("purchase_types باید فهرستی از نوع تراکنش‌ها باشد.")
            out[k] = [x.strip()[:40] for x in v][:20]
        elif k == "bot_timezone":
            out[k] = str(v)[:64]
        elif k == "referrer_pool":
            if not isinstance(v, list):
                raise AppError("فهرست معرف‌ها باید آرایه‌ای از شناسه‌های عددی باشد.")
            ids: list[str] = []
            for x in v:
                x = str(x).strip()
                if not x:
                    continue
                if not x.isdigit() or not 3 <= len(x) <= 20:
                    raise AppError(f"«{x}» شناسه‌ی عددی تلگرام نیست.")
                if x not in ids:
                    ids.append(x)
            out[k] = ids[:500]
        elif k == "random_tracking":
            raise AppError("نتیجه‌ی آزمایش ردیابی کد رندوم فقط با خود آزمایش عوض می‌شود.")
    return out


async def list_accounts(s: AsyncSession) -> list[Account]:
    return list((await s.execute(select(Account).order_by(Account.id))).scalars())


async def by_slug(s: AsyncSession, slug: str) -> Account:
    acc = (await s.execute(select(Account).where(Account.slug == slug))).scalar_one_or_none()
    if acc is None:
        raise NotFound("این حساب پیدا نشد.")
    return acc


async def create(s: AsyncSession, name: str, slug: str, color: str | None = None) -> Account:
    name = name.strip()
    slug = slug.strip().lower()
    if not name:
        raise AppError("نام حساب را وارد کنید.")
    if not SLUG_RE.match(slug):
        raise AppError("نشانی کوتاه فقط حروف کوچک انگلیسی، عدد و خط تیره (۳ تا ۴۰ کاراکتر) می‌تواند باشد.")
    if (await s.execute(select(Account.id).where(Account.slug == slug))).first():
        raise Conflict("این نشانی کوتاه قبلاً استفاده شده.")
    count = (await s.execute(select(func.count(Account.id)))).scalar_one()
    acc = Account(
        name=name[:80],
        slug=slug,
        color=color if color in COLORS else COLORS[count % len(COLORS)],
        settings={},
        sync_state={},
        bot_stats={},
        bot_fields={},
    )
    s.add(acc)
    await s.flush()
    events.log(s, "account_created", account_id=acc.id, actor="user", detail=name)
    return acc


async def update(
    s: AsyncSession,
    acc: Account,
    *,
    name: str | None = None,
    color: str | None = None,
    settings_patch: dict[str, Any] | None = None,
    automation_paused: bool | None = None,
) -> Account:
    if name is not None:
        if not name.strip():
            raise AppError("نام حساب را وارد کنید.")
        acc.name = name.strip()[:80]
    if color is not None:
        if color not in COLORS:
            raise AppError("رنگ نامعتبر است.")
        acc.color = color
    if settings_patch:
        clean = validate_settings_patch(settings_patch)
        acc.settings = {**(acc.settings or {}), **clean}
        events.log(s, "settings", account_id=acc.id, actor="user", detail=", ".join(sorted(clean)))
    if automation_paused is not None and automation_paused != acc.automation_paused:
        acc.automation_paused = automation_paused
        events.log(
            s,
            "automation_paused" if automation_paused else "automation_resumed",
            account_id=acc.id,
            actor="user",
            level="warn" if automation_paused else "info",
        )
    return acc


# ---- Telegram Ads connection ----


def tg_client(acc: Account) -> TelegramAdsClient:
    token = crypto.decrypt(acc.tg_token_enc)
    if not token:
        raise AppError("توکن Telegram Ads برای این حساب وارد نشده است.", code="tg_not_configured")
    return TelegramAdsClient(token, currency=acc.currency or "")


def apply_account_info(acc: Account, info: dict[str, Any]) -> None:
    acc.tg_account_id = str(info.get("account_id") or "")[:120]
    acc.tg_title = str(info.get("title") or "")[:200]
    acc.currency = str(info.get("currency") or acc.currency or "")[:8]
    acc.spent_budget = float(info.get("spent_budget") or 0)
    acc.remaining_budget = float(info.get("remaining_budget") or 0)
    acc.ads_budget = float(info.get("ads_budget") or 0)


async def check_telegram(s: AsyncSession, acc: Account) -> dict[str, Any]:
    acc.tg_checked_ts = now_ts()
    try:
        async with tg_client(acc) as c:
            info = await c.account()
    except TelegramAdsError as e:
        acc.tg_status, acc.tg_error = "error", e.friendly
        events.log(s, "telegram_check_failed", account_id=acc.id, detail=e.error, level="warn")
        return {"ok": False, "error": e.friendly, "code": e.error}
    except AppError as e:
        acc.tg_status, acc.tg_error = "unset", e.detail
        return {"ok": False, "error": e.detail, "code": e.code}
    apply_account_info(acc, info)
    acc.tg_status, acc.tg_error = "ok", ""
    return {"ok": True, "title": acc.tg_title, "currency": acc.currency, "remaining_budget": acc.remaining_budget}


def reset_sync(acc: Account, kinds: tuple[str, ...]) -> None:
    """New credentials: forget the failure backoff so the next sync runs right away."""
    state = dict(acc.sync_state or {})
    for kind in kinds:
        st = dict(state.get(kind) or {})
        st.update(fails=0, last_try=0, error="")
        state[kind] = st
    acc.sync_state = state


async def set_telegram_token(s: AsyncSession, acc: Account, token: str) -> dict[str, Any]:
    token = token.strip()
    if len(token) < 8 or any(ch.isspace() for ch in token):
        raise AppError("توکن Telegram Ads معتبر به نظر نمی‌رسد. آن را از Telegram Ads ← API ← Copy Token کپی کنید.")
    acc.tg_token_enc = crypto.encrypt(token)
    acc.tg_token_last4 = crypto.last4(token)
    reset_sync(acc, ("fast", "stats"))
    events.log(s, "telegram_token_set", account_id=acc.id, actor="user")
    return await check_telegram(s, acc)


async def clear_telegram_token(s: AsyncSession, acc: Account) -> None:
    acc.tg_token_enc, acc.tg_token_last4, acc.tg_status, acc.tg_error = None, "", "unset", ""
    events.log(s, "telegram_token_removed", account_id=acc.id, actor="user", level="warn")


# ---- Sales bot connection ----


def bot_client(acc: Account) -> BotApiClient:
    key = crypto.decrypt(acc.bot_api_key_enc)
    if not acc.bot_base_url or not key:
        raise AppError("آدرس و کلید API ربات برای این حساب وارد نشده است.", code="bot_not_configured")
    return BotApiClient(acc.bot_base_url, key)


async def check_bot(s: AsyncSession, acc: Account) -> dict[str, Any]:
    acc.bot_checked_ts = now_ts()
    checks: list[dict[str, Any]] = []
    try:
        client = bot_client(acc)
    except AppError as e:
        acc.bot_status, acc.bot_error = "unset", e.detail
        return {"ok": False, "status": "unset", "error": e.detail, "checks": []}
    fields = dict(acc.bot_fields or {})
    async with client as c:
        try:
            stats = await c.statbot()
            acc.bot_stats = stats
            checks.append(
                {
                    "name": "statbot",
                    "label": "آمار ربات",
                    "ok": True,
                    "message": f"{stats.get('count_user', 0)} کاربر، {stats.get('count_invoice', 0)} سرویس",
                }
            )
        except BotApiError as e:
            checks.append({"name": "statbot", "label": "آمار ربات", "ok": False, "message": e.friendly})
        label = "تراکنش‌ها (users → transactions)"
        try:
            rows = await c.transactions(limit=5)
            fields["transactions"] = field_names(rows)
            checks.append(
                {"name": "transactions", "label": label, "ok": True, "message": f"{len(rows)} ردیف نمونه خوانده شد"}
            )
        except BotApiError as e:
            types = [str(t) for t in settings_of(acc).get("purchase_types") or [] if str(t)]
            per_type = None
            if retryable(e) and types:
                with contextlib.suppress(BotApiError):
                    per_type = await c.transactions(limit=5, tx_type=types[0])
            if per_type is not None:
                if per_type:
                    fields["transactions"] = field_names(per_type)
                checks.append(
                    {
                        "name": "transactions",
                        "label": label,
                        "ok": True,
                        "message": (
                            f"گزارش کامل تراکنش‌ها در ربات خطا می‌دهد ({e.message})، ولی با فیلتر نوع خرید "
                            f"({', '.join(types)}) کار می‌کند؛ فروش از همین راه خوانده می‌شود."
                        ),
                    }
                )
            else:
                checks.append({"name": "transactions", "label": label, "ok": False, "message": e.friendly})
    acc.bot_fields = fields
    ok_count = sum(1 for c in checks if c["ok"])
    if ok_count == len(checks):
        acc.bot_status, acc.bot_error = "ok", ""
    elif ok_count:
        acc.bot_status = "warn"
        acc.bot_error = next(c["message"] for c in checks if not c["ok"])
    else:
        acc.bot_status = "error"
        acc.bot_error = checks[0]["message"] if checks else "خطا"
    if acc.bot_status != "ok":
        events.log(s, "bot_check_failed", account_id=acc.id, detail=acc.bot_error, level="warn")
    return {
        "ok": acc.bot_status == "ok",
        "status": acc.bot_status,
        "error": acc.bot_error,
        "checks": checks,
        "stats": acc.bot_stats,
    }


async def set_bot(
    s: AsyncSession, acc: Account, *, base_url: str, api_key: str | None, username: str
) -> dict[str, Any]:
    try:
        base = normalize_base_url(base_url)
    except ValueError as e:
        raise AppError(str(e)) from None
    if not base:
        raise AppError("آدرس ربات را وارد کنید (مثل https://bot.example.com).")
    username = username.strip().lstrip("@")
    if username and not USERNAME_RE.match(username):
        raise AppError("نام کاربری ربات معتبر نیست (مثل sychonetbot).")
    acc.bot_base_url = base
    acc.bot_username = username
    if api_key is not None and api_key.strip():
        acc.bot_api_key_enc = crypto.encrypt(api_key.strip())
        acc.bot_key_last4 = crypto.last4(api_key)
    if not acc.bot_api_key_enc:
        raise AppError("کلید API ربات را وارد کنید.")
    reset_sync(acc, ("bot",))
    events.log(s, "bot_connection_set", account_id=acc.id, actor="user", detail=base)
    return await check_bot(s, acc)


async def lookup_referrer(acc: Account, chat_id: str) -> dict[str, Any]:
    """Confirm a referrer account exists in the bot and count who it already referred."""
    async with bot_client(acc) as c:
        try:
            await c.user(chat_id)
        except BotApiError as e:
            return {"ok": False, "error": e.friendly}
        try:
            rows = extract_rows(await c.request("GET", "/api/users", "affiliates_users", chat_id=chat_id, limit=1000))
        except BotApiError as e:
            return {"ok": True, "referred": None, "warning": e.friendly}
    return {"ok": True, "referred": len(rows), "fields": field_names(rows)}


def public(acc: Account) -> dict[str, Any]:
    st = settings_of(acc)
    return {
        "id": acc.id,
        "slug": acc.slug,
        "name": acc.name,
        "color": acc.color,
        "created_at": acc.created_at.isoformat() if acc.created_at else None,
        "telegram": {
            "configured": bool(acc.tg_token_enc),
            "token_last4": acc.tg_token_last4,
            "status": acc.tg_status,
            "error": acc.tg_error,
            "checked_ts": acc.tg_checked_ts,
            "account_id": acc.tg_account_id,
            "title": acc.tg_title,
            "currency": acc.currency,
            "spent_budget": acc.spent_budget,
            "remaining_budget": acc.remaining_budget,
            "ads_budget": acc.ads_budget,
        },
        "bot": {
            "configured": bool(acc.bot_api_key_enc and acc.bot_base_url),
            "base_url": acc.bot_base_url,
            "username": acc.bot_username,
            "key_last4": acc.bot_key_last4,
            "status": acc.bot_status,
            "error": acc.bot_error,
            "checked_ts": acc.bot_checked_ts,
            "stats": acc.bot_stats or {},
            "fields": acc.bot_fields or {},
        },
        "settings": st,
        "sync": {k: v for k, v in (acc.sync_state or {}).items() if k in ("fast", "stats", "bot")},
        "bot_diag_ts": ((acc.sync_state or {}).get("bot_diag") or {}).get("ts", 0),
        "automation_paused": acc.automation_paused,
    }


async def import_legacy_token(s: AsyncSession) -> None:
    """v1 kept one token in .env. Create the first account from it once, so upgrades keep working."""
    from app.services import app_settings

    token = get_settings().legacy_tg_ads_token.get_secret_value().strip()
    if not token or await app_settings.get(s, "legacy_imported"):
        return
    await app_settings.put(s, "legacy_imported", True)
    if (await s.execute(select(func.count(Account.id)))).scalar_one():
        return
    acc = await create(s, "سایکونت", "psychonet")
    acc.tg_token_enc = crypto.encrypt(token)
    acc.tg_token_last4 = crypto.last4(token)
    acc.bot_username = "sychonetbot"
    events.log(s, "legacy_imported", account_id=acc.id, detail="توکن نسخه‌ی ۱ وارد شد")
