"""Bulk operations: one confirmed change applied to many ads, step by step through the job queue.

Kinds: "content" (new text and/or photo; the photo only reaches ads whose placement shows one),
"referral" (referral links: from the referrer pool, one shared link, or a random code per ad) and
"replace" (a compliant replacement for ads Telegram declined or stopped showing).

Two modes:
  inplace   editAd on the same ad: statistics stay, the ad is hidden again until Telegram re-approves.
  replace   a new ad with the same placement, CPM and target. Only once Telegram approves it the old
            ad is paused, its budget withdrawn 10 minutes later (AD_ACTIVE_RECENTLY), optionally moved to
            the new ad, and the old ad deleted (if the API has deleteAd) or archived. A declined new ad
            leaves the old one untouched.

Every Telegram call is a Job with an idempotency key. Ads are submitted in batches (size, interval and
how many may wait in review are account settings) so the rate limit is respected and not every ad goes
to review at once. `advance()` runs after each fast sync and after bulk jobs finish.

Money in replace mode: a new ad needs at least `min_initial_budget` (Telegram's minimum, 1 TON) from the
account balance when it is created; the old ad's budget can only come back after the new one is approved
and the old one has been paused for 10 minutes. With too little balance for every new ad, replacements
go out in waves: as many as the balance pays for, the next wave once withdrawals of the previous wave are
back. Only the excess of the old budget over the minimum is moved to the new ad ("transfer" jobs, which
add no money and do not count against the top-up cap); initial budgets have their own daily cap.

Item status: pending → submitted → in_review → approved → (replace: retired) | declined | failed | skipped.
"""

import json
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFound
from app.core.logging import get_logger
from app.db.base import now_ts
from app.db.session import session_scope
from app.integrations.telegram_ads import TelegramAdsError
from app.models import Account, Ad, BulkItem, BulkOperation, Job, Proposal, TextVariant
from app.services import (
    accounts,
    actions,
    app_settings,
    budget,
    events,
    guidelines,
    metrics,
    placement,
    referrals,
    tracking,
)
from app.services.accounts import settings_of
from app.services.sync import start_code_of, target_summary

log = get_logger("bulk")
KINDS = ("content", "referral", "replace")
MODES = ("replace", "inplace")
OPEN_ITEM = ("pending", "submitted", "in_review")
FINAL_ITEM = ("retired", "failed", "skipped", "declined")
CAP_KEY = "tg_capabilities"
STATUS_LABEL = {
    "pending": "در انتظار",
    "submitted": "ارسال شد",
    "in_review": "در بررسی تلگرام",
    "approved": "تأیید شد",
    "declined": "رد شد",
    "retired": "قدیمی بازنشسته شد",
    "failed": "ناموفق",
    "skipped": "رد شد از عملیات",
}
REVIEW_WARNING = "به بررسی تلگرام می‌رود و تا تأیید نمایش داده نمی‌شود."


# ---------------------------------------------------------------- capabilities


async def capabilities(s: AsyncSession) -> dict[str, Any]:
    cap = await app_settings.get(s, CAP_KEY, None) or {}
    return {
        "delete_ad": cap.get("delete_ad", "unknown"),
        "edit_photo": cap.get("edit_photo", "unknown"),
        "checked_ts": cap.get("checked_ts", 0),
        "source": cap.get("source", ""),
    }


async def set_capability(s: AsyncSession, name: str, value: str, source: str) -> None:
    cap = dict(await app_settings.get(s, CAP_KEY, None) or {})
    cap[name] = value
    cap["checked_ts"] = now_ts()
    cap["source"] = source
    await app_settings.put(s, CAP_KEY, cap)


async def probe(s: AsyncSession, acc: Account) -> dict[str, Any]:
    """Safe probes (no ad can change): see TelegramAdsClient.probe_capabilities."""
    try:
        async with accounts.tg_client(acc) as c:
            found = await c.probe_capabilities()
    except TelegramAdsError as e:
        raise AppError(f"آزمایش انجام نشد: {e.friendly}") from None
    cur = await capabilities(s)
    cap = {
        "delete_ad": found["delete_ad"],
        # A real one-ad test that already answered beats an inconclusive probe.
        "edit_photo": found["edit_photo"] if found["edit_photo"] != "unknown" else cur["edit_photo"],
        "checked_ts": now_ts(),
        "source": "probe",
    }
    await app_settings.put(s, CAP_KEY, cap)
    events.log(s, "tg_capabilities", account_id=acc.id, actor="user", detail=json.dumps(found))
    return await capabilities(s)


# ---------------------------------------------------------------- planning (preview)


def create_target(ad: Ad) -> tuple[dict[str, Any], str] | None:
    """createAd target and placement for a replacement of `ad`, or None for targets we cannot copy safely."""
    t = ad.target or {}
    typ = t.get("type") or ad.target_type
    if typ == "channels":
        ids = [
            ("@" + str(x["username"])) if x.get("username") else str(x.get("channel_id") or "")
            for x in t.get("channels") or []
        ]
        ids = [i for i in ids if i and i != "@"]
        return ({"type": "channels", "channel_ids": ids}, ad.placement or "channel_post") if ids else None
    if typ == "bots":
        ids = [
            ("@" + str(x["username"])) if x.get("username") else str(x.get("bot_id") or "") for x in t.get("bots") or []
        ]
        ids = [i for i in ids if i and i != "@"]
        return ({"type": "bots", "bot_ids": ids}, ad.placement or "bot_banner") if ids else None
    return None


def transfer_amount(old: float, source: str, move_old: bool, min_budget: float, cap: float) -> float:
    """What goes from the withdrawn old budget to the new ad.

    "transfer": the new ad started with the minimum from the balance, so only the excess of the old budget
    over that minimum moves (the minimum stays in the balance for the next wave). "account" with
    topup_after_withdraw: the whole old budget. Never more than one top-up (topup_max_per_ad).
    """
    if not move_old or old <= budget.EMPTY:
        return 0.0
    amount = max(0.0, old - min_budget) if source == "transfer" else old
    return round(min(amount, cap), 5)


def _blocking(check: dict[str, Any]) -> list[str]:
    return [i["message"] for i in check["issues"] if i["severity"] == "error"]


async def _busy_ads(s: AsyncSession, acc: Account) -> set[int]:
    out: set[int] = set()
    for old, new in await s.execute(
        select(BulkItem.ad_id, BulkItem.new_ad_id)
        .join(BulkOperation, BulkOperation.id == BulkItem.op_id)
        .where(BulkItem.account_id == acc.id, BulkOperation.status == "running", BulkItem.status.in_(OPEN_ITEM))
    ):
        out.add(int(old))
        if new:
            out.add(int(new))
    return out


async def plan(s: AsyncSession, acc: Account, body: dict[str, Any], *, live_balance: bool = True) -> dict[str, Any]:
    """Exactly what would happen to each ad, the money moved and every warning. Nothing is changed."""
    st = settings_of(acc)
    kind = body.get("kind")
    if kind not in KINDS:
        raise AppError("نوع عملیات نامعتبر است.")
    mode = "replace" if kind == "replace" else body.get("mode") or "replace"
    if mode not in MODES:
        raise AppError("حالت عملیات نامعتبر است.")
    ad_ids = [int(x) for x in body.get("ad_ids") or []]
    if not ad_ids:
        raise AppError("هیچ آگهی‌ای انتخاب نشده است.")
    if len(ad_ids) > 500:
        raise AppError("حداکثر ۵۰۰ آگهی در یک عملیات.")
    rows = {r["ad_id"]: r for r in await metrics.ad_rows(s, acc) if r["ad_id"] in set(ad_ids)}
    ads = {
        a.ad_id: a for a in (await s.execute(select(Ad).where(Ad.account_id == acc.id, Ad.ad_id.in_(ad_ids)))).scalars()
    }
    busy = await _busy_ads(s, acc)
    caps = await capabilities(s)
    errors: list[str] = []
    warnings: list[str] = []

    text = (body.get("text") or "").strip() or None
    variant_id = body.get("variant_id")
    if variant_id and not text:
        v = await s.get(TextVariant, int(variant_id))
        if v is None or v.account_id != acc.id:
            raise AppError("متن انتخاب‌شده پیدا نشد.")
        text = v.text.strip()
    per_ad = {int(k): (v.get("text") or "").strip() for k, v in (body.get("per_ad") or {}).items() if v}
    photo_id = (body.get("photo_id") or "").strip() or None
    if kind in ("content", "replace") and not (text or per_ad or photo_id):
        raise AppError("متن تازه یا عکس تازه را وارد کنید.")

    # Photo: in place only where the API is known (or being tested on one ad) to change a live ad's photo.
    photo_test = False
    if photo_id and mode == "inplace":
        if caps["edit_photo"] == "no":
            errors.append(
                "Telegram Ads API عوض کردن عکس آگهی موجود را پشتیبانی نمی‌کند؛ حالت «جایگزینی» را انتخاب کنید."
            )
        elif caps["edit_photo"] == "unknown":
            if len(ad_ids) == 1:
                photo_test = True
                warnings.append(
                    "هنوز معلوم نیست API عکس آگهی موجود را عوض می‌کند. این عملیات روی همین یک آگهی آزمایش می‌کند و "
                    "نتیجه برای عملیات بعدی ذخیره می‌شود."
                )
            else:
                errors.append(
                    "پشتیبانی عوض کردن عکس در جا هنوز آزمایش نشده؛ یا حالت «جایگزینی» را انتخاب کنید یا اول روی یک آگهی آزمایش کنید."
                )

    # Referral links: from the pool, one shared link, or a random code per ad (made at creation).
    assignments: list[dict[str, Any]] = []
    url_for: dict[int, tuple[str, str | None]] = {}  # ad_id -> (promote_url, referrer_id)
    ref_mode = body.get("referral_mode") or "pool"
    random_tracking = st["random_tracking"]
    if kind == "referral":
        if not acc.bot_username:
            errors.append("نام کاربری ربات در تنظیمات حساب وارد نشده است.")
        base_url = f"https://t.me/{acc.bot_username}"
        if ref_mode == "pool":
            pool = await referrals.pool_status(s, acc)
            targets = [rows[i] for i in ad_ids if i in rows and not rows[i]["tag_id"]]
            assignments, w = referrals.assign(targets, pool, body.get("group_by") or "target")
            warnings.extend(w)
            if not assignments and targets:
                errors.append("معرف آزادی برای ساخت برچسب نیست.")
            for a in assignments:
                for aid in a["ad_ids"]:
                    url_for[aid] = (f"{base_url}?start={a['referrer_id']}", a["referrer_id"])
        elif ref_mode == "shared":
            shared = await referrals.resolve_shared(
                s, acc, body.get("shared_referrer_id"), body.get("shared_tag_id"), check=live_balance
            )
            if shared.get("error"):
                errors.append(shared["error"])
            else:
                assignments = [
                    {
                        "referrer_id": shared["referrer_id"],
                        "tag_id": shared["tag_id"],
                        "label": shared["label"],
                        "ad_ids": [i for i in ad_ids if i in rows],
                    }
                ]
                for aid in assignments[0]["ad_ids"]:
                    url_for[aid] = (f"{base_url}?start={shared['start_code']}", shared["referrer_id"])
                warnings.append(
                    "همه‌ی این آگهی‌ها یک لینک می‌گیرند: فروش فقط برای کل این گروه دیده می‌شود، نه برای هر آگهی."
                )
                if shared.get("warning"):
                    warnings.append(shared["warning"])
        elif ref_mode == "random":
            if not (acc.bot_base_url and acc.bot_api_key_enc):
                errors.append("برای ساخت کد رندوم، اتصال API ربات لازم است (کدها با ربات چک می‌شوند).")
            for aid in ad_ids:
                url_for[aid] = (f"{base_url}?start=<کد ۱۰ رقمی تصادفی>", None)
            if random_tracking != "works":
                warnings.append(
                    "⚠ هنوز معلوم نیست ربات کاربرانی را که با کد ناشناخته می‌آیند ثبت می‌کند"
                    + (" (آزمایش نشان داد ثبت نمی‌کند)" if random_tracking == "ignored" else "")
                    + ". لینک‌ها کار می‌کنند ولی ممکن است فروش این آگهی‌ها در پنل جدا دیده نشود. "
                    "پیشنهاد: اول «آزمایش ردیابی کد رندوم» را در تنظیمات حساب انجام دهید، یا از توسعه‌دهنده‌ی ربات "
                    "بخواهید کد start هر کاربر را ذخیره کند."
                )
        else:
            raise AppError("نوع لینک رفرال نامعتبر است.")

    budget_source = body.get("budget_source") or "transfer"
    if budget_source not in ("account", "transfer"):
        raise AppError("منبع بودجه نامعتبر است.")
    min_budget = float(st["min_initial_budget"])
    try:
        initial = float(body.get("initial_budget") or max(st["default_budget"], min_budget))
    except (TypeError, ValueError):
        raise AppError("بودجه‌ی اولیه باید عدد باشد.") from None
    move_old = budget_source == "transfer" or bool(body.get("topup_after_withdraw"))
    retire = "delete" if caps["delete_ad"] == "yes" else "archive"
    cur = acc.currency or "TON"
    if mode == "replace" and budget_source == "account":
        if initial + 1e-9 < min_budget:
            errors.append(f"بودجه‌ی اولیه‌ی هر آگهی دست‌کم {min_budget:g} {cur} است؛ تلگرام آگهی با بودجه‌ی کمتر نمی‌سازد.")
        elif initial > st["topup_max_per_ad"] * 5 + 1e-9:
            errors.append(f"بودجه‌ی اولیه‌ی هر آگهی حداکثر {st['topup_max_per_ad'] * 5:g} {cur} است.")

    items: list[dict[str, Any]] = []
    from_account = from_old = to_new = net_extra_total = 0.0
    for aid in ad_ids:
        ad, r = ads.get(aid), rows.get(aid)
        it: dict[str, Any] = {
            "ad_id": aid,
            "label": (r or {}).get("target_label") or (ad.title if ad else "") or f"#{aid}",
            "placement": ad.placement if ad else "",
            "changes": [],
            "money": {"from_account": 0.0, "from_old": 0.0, "to_new": 0.0},
            "warnings": [],
            "blocked": "",
            "skipped": "",
            "photo": False,
            "params": {},
        }
        items.append(it)
        if ad is None or ad.gone:
            it["blocked"] = "این آگهی پیدا نشد."
            continue
        if ad.archived:
            it["blocked"] = "آگهی بایگانی شده است."
            continue
        if aid in busy:
            it["blocked"] = "این آگهی در یک عملیات گروهی دیگر در حال اجراست."
            continue
        new_text = per_ad.get(aid) or text
        new_url = ad.promote_url
        if kind == "referral":
            if ref_mode == "pool" and ad.tag_id:
                it["skipped"] = "از قبل برچسب رفرال دارد."
                continue
            if aid not in url_for:
                it["blocked"] = "معرفی برای این آگهی نماند."
                continue
            new_url, rid = url_for[aid]
            if new_url == ad.promote_url:
                it["skipped"] = "همین لینک را دارد."
                continue
            it["params"]["referrer_id"] = rid
            if ref_mode == "random":
                it["params"]["random_code"] = True
            it["changes"].append({"field": "promote_url", "label": "لینک", "old": ad.promote_url, "new": new_url})
        if new_text and new_text != ad.text:
            chk = guidelines.check(new_text, promote_url=new_url)
            bad = _blocking(chk)
            if bad:
                it["blocked"] = "متن با قوانین تلگرام نمی‌خواند: " + "؛ ".join(bad)
                continue
            it["warnings"].extend(i["message"] for i in chk["issues"] if i["severity"] == "warn")
            it["changes"].append({"field": "text", "label": "متن", "old": ad.text, "new": new_text})
            it["params"]["text"] = new_text
        elif mode == "replace" and not new_text:
            bad = _blocking(guidelines.check(ad.text, promote_url=new_url))
            if bad:  # a replacement must not copy a text Telegram would decline again
                it["blocked"] = "متن فعلی با قوانین تلگرام نمی‌خواند و جایگزین با همان متن ساخته نمی‌شود: " + "؛ ".join(
                    bad
                )
                continue
            it["params"]["text"] = ad.text
        if photo_id and placement.supports_photo(ad):
            it["changes"].append(
                {"field": "photo", "label": "عکس", "old": "عکس فعلی" if ad.has_photo else "بدون عکس", "new": "عکس تازه"}
            )
            it["params"]["photo_id"] = photo_id
            it["photo"] = True
        if new_url != ad.promote_url:
            it["params"]["promote_url"] = new_url
        if not it["changes"] and kind != "replace":
            # Photo only, on an ad whose placement has no photo (a bot banner): left out quietly.
            it["skipped"] = (
                "رد شد — آگهی ربات عکس ندارد"
                if photo_id and not placement.supports_photo(ad)
                else "تغییری برای این آگهی نیست."
            )
            continue
        if mode == "inplace":
            it["warnings"].insert(
                0, "این آگهی دوباره به بررسی تلگرام می‌رود و تا تأیید نمایش داده نمی‌شود؛ آمارش حفظ می‌شود."
            )
            continue
        tgt = create_target(ad)
        if tgt is None:
            it["blocked"] = (
                "هدف‌گیری این آگهی (غیر از کانال/ربات مشخص) برای ساخت خودکار جایگزین پشتیبانی نمی‌شود؛ «ویرایش در جا» را انتخاب کنید."
            )
            continue
        if ad.has_photo and not photo_id:
            it["warnings"].append("آگهی قدیمی عکس دارد؛ جایگزین بدون عکس ساخته می‌شود مگر عکس تازه بدهید.")
        old_budget = ad.remaining_budget or 0
        start = min_budget if budget_source == "transfer" else initial
        moved = transfer_amount(old_budget, budget_source, move_old, min_budget, st["topup_max_per_ad"])
        net_extra = max(0.0, start - old_budget) if budget_source == "transfer" else 0.0
        it["money"] = {
            "from_account": round(start, 5),
            "from_old": round(old_budget, 5),
            "to_new": round(start + moved, 5),
            "net_extra": round(net_extra, 5),
        }
        it["params"]["initial_budget"] = round(start, 5)
        from_account += start
        from_old += old_budget
        to_new += start + moved
        net_extra_total += net_extra
        if net_extra > budget.EMPTY:
            it["warnings"].append(
                f"بودجه‌ی آگهی قدیمی ({old_budget:.4g}) کمتر از حداقل {min_budget:g} {cur} است؛ "
                f"این جایگزینی {net_extra:.4g} {cur} هزینه‌ی خالص اضافه دارد."
            )
        it["warnings"].insert(0, "آگهی جدید " + REVIEW_WARNING)
        tail = "حذف" if retire == "delete" else "بایگانی (متوقف، بدون بودجه، پنهان از فهرست)"
        it["warnings"].append(
            f"فقط بعد از تأیید آگهی جدید، آگهی قدیمی متوقف می‌شود، ۱۰ دقیقه بعد بودجه‌اش ({old_budget:.4g}) برداشت و "
            + (
                f"به موجودی حساب برمی‌گردد و {moved:.4g} {cur} (مازاد بر بودجه‌ی اولیه) به آگهی جدید منتقل "
                if moved
                else "به موجودی حساب برگردانده "
            )
            + f"می‌شود و سپس {tail} می‌شود. اگر آگهی جدید رد شود، آگهی قدیمی دست نمی‌خورد."
        )
        full = max(0.0, old_budget - min_budget) if budget_source == "transfer" else old_budget
        if move_old and full > st["topup_max_per_ad"] + 1e-9:
            it["warnings"].append(
                f"سقف هر شارژ {st['topup_max_per_ad']:g} است؛ فقط همین مقدار به آگهی جدید می‌رود و بقیه در موجودی حساب می‌ماند."
            )

    runnable = [i for i in items if not i["blocked"] and not i["skipped"]]
    skipped = [i for i in items if i["skipped"]]
    if not runnable:
        errors.append("هیچ‌کدام از آگهی‌های انتخاب‌شده تغییری ندارد یا قابل اجرا نیست؛ دلیل هر کدام را ببینید.")
    photo_summary = ""
    if photo_id:
        photo_ads = sum(1 for i in runnable if i["photo"])
        text_only = sum(1 for i in runnable if not i["photo"])
        no_photo_skipped = sum(1 for i in skipped if "عکس ندارد" in i["skipped"])
        parts = [f"عکس برای {photo_ads} آگهی کانال عوض می‌شود"]
        if text_only:
            parts.append(f"{text_only} آگهی ربات فقط متنشان عوض می‌شود")
        if no_photo_skipped:
            parts.append(f"{no_photo_skipped} آگهی ربات بدون تغییر می‌مانند (آگهی ربات عکس ندارد)")
        photo_summary = "؛ ".join(parts) + "."
    balance = acc.remaining_budget
    if mode == "replace" and from_account > 0 and live_balance:
        try:
            async with accounts.tg_client(acc) as c:
                balance = float((await c.account()).get("remaining_budget") or 0)
        except (TelegramAdsError, AppError):
            warnings.append("موجودی تازه از تلگرام خوانده نشد؛ آخرین موجودی ذخیره‌شده ملاک است.")
    budget_line = ""
    first_wave = len(runnable)
    if mode == "replace" and runnable:
        each = min_budget if budget_source == "transfer" else initial
        n = len(runnable)
        budget_line = (
            f"برای ساخت {n} آگهی جدید، {n} × {each:g} = {each * n:.4g} {cur} لازم است؛ "
            f"موجودی فعلی {balance:.4g} {cur} است."
        )
        first_wave = min(n, int((balance + 1e-9) // each)) if each > 0 else n
        if first_wave == 0:
            errors.append(
                f"موجودی حساب ({balance:.4g} {cur}) حتی برای ساخت یک آگهی جدید با حداقل بودجه‌ی "
                f"{each:g} {cur} کافی نیست. حساب را شارژ کنید."
            )
        elif first_wave < n:
            warnings.append(
                f"موجودی برای همه‌ی آگهی‌های جدید کافی نیست؛ عملیات موج‌به‌موج اجرا می‌شود: موج اول {first_wave} آگهی، "
                "و هر موج بعدی وقتی بودجه‌ی آگهی‌های قدیمیِ موج قبل (بعد از تأیید آگهی جدید و ۱۰ دقیقه توقف) "
                "به موجودی برگشت. هر وقت خواستید می‌توانید عملیات را متوقف کنید."
            )
            returned = sum(i["money"]["from_old"] for i in runnable) - sum(
                i["money"]["to_new"] - i["money"]["from_account"] for i in runnable
            )
            if balance + returned + 1e-9 < each * n:
                warnings.append(
                    "حتی بعد از برگشت بودجه‌ی همه‌ی آگهی‌های قدیمی هم موجودی برای همه‌ی جایگزین‌ها کافی نیست؛ "
                    "آگهی‌های آخر منتظر شارژ حساب می‌مانند."
                )
    if net_extra_total > budget.EMPTY:
        warnings.append(
            f"آگهی‌هایی که بودجه‌ی قدیمی‌شان کمتر از {min_budget:g} {cur} است در مجموع {net_extra_total:.4g} {cur} "
            "هزینه‌ی خالص اضافه دارند (بودجه‌ی اولیه‌ی آگهی جدید بیشتر از بودجه‌ای است که برمی‌گردد)."
        )
    new_used = await actions.new_budget_last_24h(s, acc)
    new_left = max(0.0, st["bulk_daily_new_budget_cap"] - new_used)
    if mode == "replace" and from_account > new_left + 1e-9:
        warnings.append(
            f"بودجه‌ی اولیه‌ی آگهی‌های جدید ({from_account:.4g} {cur}) از باقی‌مانده‌ی سقف روزانه‌ی آگهی جدید "
            f"({new_left:.4g} {cur}) بیشتر است؛ بقیه منتظر می‌مانند تا سقف آزاد شود."
        )
    if acc.automation_paused:
        errors.append("اقدام‌های این حساب متوقف است.")
    return {
        "kind": kind,
        "mode": mode,
        "retire": retire,
        "budget_source": budget_source,
        "move_old_budget": move_old,
        "photo_test": photo_test,
        "items": items,
        "assignments": assignments,
        "referral_mode": ref_mode if kind == "referral" else None,
        "random_tracking": random_tracking,
        "totals": {
            "ads": len(runnable),
            "blocked": sum(1 for i in items if i["blocked"]),
            "skipped": len(skipped),
            "from_account": round(from_account, 5),
            "from_old": round(from_old, 5),
            "to_new": round(to_new, 5),
            "net_extra": round(net_extra_total, 5),
            "account_balance": balance,
            "min_initial_budget": min_budget,
            "first_wave": first_wave,
            "new_budget_cap_left": round(new_left, 5),
        },
        "photo_summary": photo_summary,
        "budget_line": budget_line,
        "errors": errors,
        "warnings": warnings,
        "capabilities": caps,
        "text": text,
        "variant_id": variant_id,
        "photo_id": photo_id,
    }


async def _variant_for(s: AsyncSession, acc: Account, text: str, name: str) -> int:
    """The library text with exactly this wording, created if needed, so its performance stays comparable."""
    for v in (await s.execute(select(TextVariant).where(TextVariant.account_id == acc.id))).scalars():
        if v.text.strip() == text.strip():
            return v.id
    v = TextVariant(account_id=acc.id, name=name[:80], text=text, source="manual")
    s.add(v)
    await s.flush()
    events.log(s, "text_created", account_id=acc.id, actor="user", detail=v.name)
    return v.id


async def create(s: AsyncSession, acc: Account, body: dict[str, Any]) -> BulkOperation:
    if not body.get("confirm"):
        raise AppError("اجرای عملیات گروهی تأیید صریح می‌خواهد.")
    p = await plan(s, acc, body)
    if p["errors"]:
        raise AppError(p["errors"][0], code="bulk_invalid")
    now = now_ts()
    op = BulkOperation(
        account_id=acc.id,
        kind=p["kind"],
        mode=p["mode"],
        status="running",
        total=p["totals"]["ads"],
        created_ts=now,
        updated_ts=now,
        last_batch_ts=0,
        params={
            "text": p["text"],
            "photo_id": p["photo_id"],
            "photo_name": str(body.get("photo_name") or "")[:120],
            "budget_source": p["budget_source"],
            "move_old_budget": p["move_old_budget"],
            "retire": p["retire"],
            "photo_test": p["photo_test"],
            "group_by": body.get("group_by") or "",
            "referral_mode": p["referral_mode"],
            "proposal_ids": [int(x) for x in body.get("proposal_ids") or []],
            "totals": p["totals"],
            "wave": 1,
            "waiting_balance": False,
        },
        note="",
    )
    s.add(op)
    await s.flush()
    tags: dict[str, int] = {}
    for a in p["assignments"]:
        if a.get("tag_id"):
            tags[a["referrer_id"]] = a["tag_id"]
            continue
        tag = await referrals.ensure_tag(s, acc, a["referrer_id"], a["label"])
        tags[a["referrer_id"]] = tag.id
    random_items = [
        it for it in p["items"] if not it["blocked"] and not it["skipped"] and it["params"].get("random_code")
    ]
    codes = await tracking.new_codes(s, acc, len(random_items)) if random_items else []
    for it, code in zip(random_items, codes, strict=True):
        tag = await tracking.ensure_random_tag(s, acc, code, it["label"])
        it["params"].update(
            promote_url=f"https://t.me/{acc.bot_username}?start={code}", referrer_id=code, random_code=code
        )
        tags[code] = tag.id
    variants: dict[str, int] = {}
    for it in p["items"]:
        params = dict(it["params"])
        if it["blocked"] or it["skipped"]:
            s.add(
                BulkItem(
                    op_id=op.id,
                    account_id=acc.id,
                    ad_id=it["ad_id"],
                    status="skipped",
                    error=it["blocked"] or it["skipped"],
                    params=params,
                    created_ts=now,
                    updated_ts=now,
                )
            )
            continue
        if params.get("text"):
            t = params["text"]
            if t not in variants:
                variants[t] = await _variant_for(s, acc, t, f"عملیات گروهی {op.id}")
            params["variant_id"] = variants[t]
        if params.get("referrer_id"):
            params["tag_id"] = tags.get(params["referrer_id"])
        s.add(
            BulkItem(
                op_id=op.id,
                account_id=acc.id,
                ad_id=it["ad_id"],
                status="pending",
                params=params,
                created_ts=now,
                updated_ts=now,
            )
        )
    for pid in op.params["proposal_ids"]:
        prop = await s.get(Proposal, pid)
        if prop is not None and prop.account_id == acc.id and prop.status == "pending":
            prop.status, prop.decided_ts, prop.result = "approved", now, f"عملیات گروهی #{op.id}"
    events.log(
        s,
        "bulk_created",
        account_id=acc.id,
        actor="user",
        detail=f"#{op.id} {op.kind}/{op.mode}: {op.total} آگهی",
    )
    return op


# ---------------------------------------------------------------- reading


def item_public(it: BulkItem, ads: dict[int, Ad]) -> dict[str, Any]:
    old, new = ads.get(it.ad_id), ads.get(it.new_ad_id or -1)
    return {
        "id": it.id,
        "ad_id": it.ad_id,
        "new_ad_id": it.new_ad_id,
        "label": (old.target_label or old.title) if old else f"#{it.ad_id}",
        "status": it.status,
        "status_label": STATUS_LABEL.get(it.status, it.status),
        "step": it.step,
        "error": it.error,
        "decline_reason": it.decline_reason,
        "withdrawn": it.withdrawn,
        "text": it.params.get("text"),
        "promote_url": it.params.get("promote_url"),
        "wave": it.params.get("wave"),
        "old_status": old.status if old else None,
        "new_status": new.status if new else None,
        "updated_ts": it.updated_ts,
    }


async def op_public(s: AsyncSession, op: BulkOperation, *, with_items: bool = False) -> dict[str, Any]:
    counts = {
        k: int(v)
        for k, v in await s.execute(
            select(BulkItem.status, func.count(BulkItem.id)).where(BulkItem.op_id == op.id).group_by(BulkItem.status)
        )
    }
    finished = sum(counts.get(k, 0) for k in FINAL_ITEM) + (counts.get("approved", 0) if op.mode == "inplace" else 0)
    total = sum(counts.values())
    out: dict[str, Any] = {
        "id": op.id,
        "kind": op.kind,
        "mode": op.mode,
        "status": op.status,
        "params": {k: v for k, v in op.params.items() if k != "photo_id"}
        | {"has_photo": bool(op.params.get("photo_id"))},
        "total": total,
        "counts": counts,
        "finished": finished,
        "progress": round(finished / total, 3) if total else 1.0,
        "created_ts": op.created_ts,
        "updated_ts": op.updated_ts,
        "finished_ts": op.finished_ts,
        "note": op.note,
    }
    if with_items:
        items = list((await s.execute(select(BulkItem).where(BulkItem.op_id == op.id).order_by(BulkItem.id))).scalars())
        ids = {i.ad_id for i in items} | {i.new_ad_id for i in items if i.new_ad_id}
        ads = {
            a.ad_id: a
            for a in (await s.execute(select(Ad).where(Ad.account_id == op.account_id, Ad.ad_id.in_(ids)))).scalars()
        }
        out["items"] = [item_public(i, ads) for i in items]
    return out


async def get_op(s: AsyncSession, acc: Account, op_id: int) -> BulkOperation:
    op = await s.get(BulkOperation, op_id)
    if op is None or op.account_id != acc.id:
        raise NotFound("این عملیات پیدا نشد.")
    return op


# ---------------------------------------------------------------- user actions


async def stop(s: AsyncSession, acc: Account, op_id: int) -> int:
    """Stop an operation: queued jobs are cancelled and items not yet sent are skipped."""
    op = await get_op(s, acc, op_id)
    if op.status != "running":
        raise AppError("این عملیات در حال اجرا نیست.")
    now = now_ts()
    items = list((await s.execute(select(BulkItem).where(BulkItem.op_id == op.id))).scalars())
    ids = [i.id for i in items]
    cancelled_items: set[int] = set()
    for j in (await s.execute(select(Job).where(Job.bulk_item_id.in_(ids), Job.status == "queued"))).scalars():
        j.status, j.result = "cancelled", "عملیات گروهی متوقف شد"
        if j.bulk_item_id:
            cancelled_items.add(j.bulk_item_id)
    cancelled = len(cancelled_items)
    for it in items:
        if it.status == "pending" or (it.status == "submitted" and it.id in cancelled_items):
            it.status, it.error, it.updated_ts = "skipped", "عملیات متوقف شد", now
    op.status, op.updated_ts, op.finished_ts = "stopped", now, now
    op.note = "متوقف شد؛ آگهی‌هایی که از قبل ساخته یا ویرایش شده‌اند در تلگرام می‌مانند."
    events.log(s, "bulk_stopped", account_id=acc.id, actor="user", detail=f"#{op.id}، {cancelled} کار لغو شد")
    return cancelled


async def resubmit(s: AsyncSession, acc: Account, op_id: int, item_id: int, text: str) -> BulkItem:
    """Edit the declined ad (the new one in replace mode) with a corrected text: it goes back to review."""
    op = await get_op(s, acc, op_id)
    it = await s.get(BulkItem, item_id)
    if it is None or it.op_id != op.id:
        raise NotFound("این آگهی در عملیات پیدا نشد.")
    if it.status != "declined":
        raise AppError("فقط آگهی ردشده دوباره فرستاده می‌شود.")
    if op.status == "stopped":
        raise AppError("این عملیات متوقف شده است.")
    if acc.automation_paused:
        raise AppError("اقدام‌های این حساب متوقف است.", code="automation_paused")
    target = it.new_ad_id or it.ad_id
    ad = (await s.execute(select(Ad).where(Ad.account_id == acc.id, Ad.ad_id == target))).scalar_one_or_none()
    text = (text or "").strip()
    chk = guidelines.check(text, promote_url=ad.promote_url if ad else None)
    bad = _blocking(chk)
    if bad:
        raise AppError("متن با قوانین تلگرام نمی‌خواند: " + "؛ ".join(bad))
    params = dict(it.params)
    params["text"] = text
    params["variant_id"] = await _variant_for(s, acc, text, f"عملیات گروهی {op.id} (اصلاح)")
    it.params = params
    actions.enqueue(s, acc, "edit_ad", target, params={"text": text}, bulk_item_id=it.id, actor="user")
    it.status, it.error, it.decline_reason, it.updated_ts = "submitted", "", "", now_ts()
    op.status, op.finished_ts = "running", 0
    return it


# ---------------------------------------------------------------- engine


async def _jobs_of(s: AsyncSession, item_id: int) -> list[Job]:
    return list((await s.execute(select(Job).where(Job.bulk_item_id == item_id).order_by(Job.id))).scalars())


def _create_params(op: BulkOperation, it: BulkItem, ad: Ad) -> dict[str, Any] | None:
    tgt = create_target(ad)
    if tgt is None:
        return None
    target, slot = tgt
    title = re.sub(r"\s+", " ", f"{ad.title or ad.target_label or ad.ad_id}")[:100] + f" r{op.id}"
    params: dict[str, Any] = {
        "title": title[:120],
        "text": it.params.get("text") or ad.text,
        "promote_url": it.params.get("promote_url") or ad.promote_url,
        "cpm": ad.cpm,
        "placement": slot,
        "target": target,
        "initial_budget": float(it.params.get("initial_budget") or 0),
        "return_target": True,
    }
    if it.params.get("photo_id") and placement.supports_photo(ad):
        params["photo_id"] = it.params["photo_id"]
    return params


async def _advance_op(s: AsyncSession, acc: Account, op: BulkOperation, now: int) -> None:
    st = settings_of(acc)
    items = list((await s.execute(select(BulkItem).where(BulkItem.op_id == op.id).order_by(BulkItem.id))).scalars())
    ids = {i.ad_id for i in items} | {i.new_ad_id for i in items if i.new_ad_id}
    ads = {
        a.ad_id: a for a in (await s.execute(select(Ad).where(Ad.account_id == acc.id, Ad.ad_id.in_(ids)))).scalars()
    }
    caps = await capabilities(s)
    changed = False

    for it in items:
        if it.status == "in_review":
            cand = ads.get((it.new_ad_id if op.mode == "replace" else it.ad_id) or -1)
            if cand is None or cand.gone:
                it.status, it.error = "failed", "آگهی در تلگرام پیدا نشد"
            elif cand.status == "declined" or cand.decline_reason:
                it.status, it.decline_reason = "declined", cand.decline_reason or "تلگرام دلیلی اعلام نکرد"
                events.log(
                    s, "bulk_declined", account_id=acc.id, ad_id=cand.ad_id, level="warn", detail=it.decline_reason
                )
            elif cand.status == "active":
                it.status = "approved"
                it.step = "pause" if op.mode == "replace" else "done"
            else:
                continue
            it.updated_ts, changed = now, True
        if it.status != "approved" or it.step in ("", "done"):
            continue
        jobs = await _jobs_of(s, it.id)
        if any(j.status in ("queued", "running") for j in jobs):
            continue
        last = jobs[-1] if jobs else None
        old, new = ads.get(it.ad_id), ads.get(it.new_ad_id or -1)
        if it.step == "pause":
            actions.stop_and_withdraw(s, acc, it.ad_id, actor="bulk", bulk_item_id=it.id)
            it.step = "withdraw"
        elif it.step == "withdraw":
            if last is not None and last.status == "failed":
                it.status, it.error = "failed", f"برداشت بودجه‌ی آگهی قدیمی ناموفق بود: {last.result}"
            else:
                it.withdrawn = float((last.params or {}).get("done_amount") or 0) if last else 0.0
                it.step = "transfer" if op.params.get("move_old_budget") and it.withdrawn > budget.EMPTY else "retire"
        elif it.step == "transfer":
            amount = transfer_amount(
                it.withdrawn,
                str(op.params.get("budget_source") or "transfer"),
                True,
                float(st["min_initial_budget"]),
                float(st["topup_max_per_ad"]),
            )
            if new is not None and amount > budget.EMPTY:
                actions.enqueue(s, acc, "transfer", new.ad_id, amount, actor="bulk", bulk_item_id=it.id)
                it.step = "transfer_wait"
            else:
                it.step = "retire"
        elif it.step == "transfer_wait":
            if last is not None and last.status == "failed":
                it.error = f"انتقال بودجه به آگهی جدید ناموفق بود ({last.result}); مبلغ در موجودی حساب است."
            it.step = "retire"
        elif it.step == "retire":
            if op.params.get("retire") == "delete" and caps["delete_ad"] == "yes":
                actions.enqueue(s, acc, "delete_ad", it.ad_id, actor="bulk", bulk_item_id=it.id)
                it.step = "delete_wait"
            else:
                if old is not None:
                    old.archived, old.archived_ts = True, now
                it.status, it.step = "retired", "done"
        elif it.step == "delete_wait":
            if last is not None and last.status == "failed":
                it.error = f"حذف ناموفق بود ({last.result}); آگهی قدیمی بایگانی شد."
            if old is not None:
                old.archived, old.archived_ts = True, now
            it.status, it.step = "retired", "done"
        it.updated_ts, changed = now, True

    # Next batch of pending items. Replacements also need the balance for their initial budget: what the
    # balance cannot pay for waits for the next wave (withdrawals of the previous wave coming back).
    pending = [i for i in items if i.status == "pending"]
    in_flight = sum(1 for i in items if i.status in ("submitted", "in_review"))
    if pending and now - op.last_batch_ts >= int(st["bulk_batch_interval_seconds"]):
        room = min(int(st["bulk_batch_size"]), max(0, int(st["bulk_max_in_review"]) - in_flight))
        sent = 0
        available = acc.remaining_budget - await actions.reserved_new_budget(s, acc)
        new_used = await actions.new_budget_last_24h(s, acc)
        short_of_balance = False
        cur = acc.currency or "TON"
        for it in pending[:room]:
            ad = ads.get(it.ad_id)
            if ad is None or ad.gone:
                it.status, it.error = "failed", "آگهی در تلگرام پیدا نشد"
                continue
            if op.mode == "inplace":
                remote = {k: it.params[k] for k in ("text", "promote_url", "photo_id") if it.params.get(k)}
                actions.enqueue(s, acc, "edit_ad", ad.ad_id, params=remote, bulk_item_id=it.id, actor="bulk")
            else:
                params = _create_params(op, it, ad)
                if params is None:
                    it.status, it.error = "skipped", "هدف‌گیری این آگهی قابل کپی نیست"
                    continue
                need = float(params["initial_budget"])
                if need + 1e-9 < float(st["min_initial_budget"]):
                    it.status, it.error = "failed", f"بودجه‌ی اولیه کمتر از حداقل {st['min_initial_budget']:g} است"
                    continue
                if need > available + 1e-9:
                    short_of_balance = True
                    break
                if new_used + need > st["bulk_daily_new_budget_cap"] + 1e-9:
                    op.note = "منتظر آزاد شدن سقف روزانه‌ی بودجه‌ی آگهی جدید"
                    break
                available -= need
                new_used += need
                actions.enqueue(
                    s,
                    acc,
                    "create_ad",
                    ad.ad_id,
                    params["initial_budget"],
                    params=params,
                    bulk_item_id=it.id,
                    actor="bulk",
                )
            it.status, it.updated_ts = "submitted", now
            it.params = {**it.params, "wave": int(op.params.get("wave") or 1)}
            sent += 1
        params_now = dict(op.params)
        if sent:
            op.last_batch_ts, changed = now, True
            if params_now.get("waiting_balance"):
                params_now.update(wave=int(params_now.get("wave") or 1) + 1, waiting_balance=False)
                for it in [i for i in pending if i.status == "submitted"]:
                    it.params = {**it.params, "wave": params_now["wave"]}
            if not short_of_balance and op.note.startswith("موج"):
                op.note = ""
        if short_of_balance and not params_now.get("waiting_balance"):
            params_now["waiting_balance"] = True
            changed = True
        if short_of_balance:
            waiting_for = sum(1 for i in items if i.status == "pending")
            in_progress = any(
                i.status in ("submitted", "in_review") or (i.status == "approved" and i.step not in ("", "done"))
                for i in items
            )
            op.note = (
                f"موج {params_now.get('wave') or 1}: {waiting_for} آگهی منتظر موجودی‌اند (موجودی آزاد "
                f"{max(available, 0):.4g} {cur})؛ "
                + (
                    "با برگشت بودجه‌ی آگهی‌های قدیمیِ این موج، موج بعد خودکار شروع می‌شود."
                    if in_progress
                    else "کار دیگری در جریان نیست؛ حساب را شارژ کنید تا ادامه پیدا کند."
                )
            )
        if params_now != op.params:
            op.params = params_now

    # Finished?
    await s.flush()
    open_left = [i for i in items if i.status in OPEN_ITEM or (i.status == "approved" and i.step not in ("", "done"))]
    if not open_left:
        op.status, op.finished_ts = "done", now
        changed = True
        declined = sum(1 for i in items if i.status == "declined")
        events.log(
            s,
            "bulk_done",
            account_id=acc.id,
            actor="bulk",
            detail=f"#{op.id} تمام شد" + (f"؛ {declined} آگهی رد شد" if declined else ""),
        )
    if changed:
        op.updated_ts = now


async def advance(account_id: int) -> int:
    """Move every running operation of the account one step. Returns how many operations were looked at."""
    async with session_scope() as s:
        acc = await s.get(Account, account_id)
        if acc is None:
            return 0
        ops = list(
            (
                await s.execute(
                    select(BulkOperation).where(BulkOperation.account_id == acc.id, BulkOperation.status == "running")
                )
            ).scalars()
        )
        if acc.automation_paused:
            return 0
        now = now_ts()
        for op in ops:
            await _advance_op(s, acc, op, now)
        return len(ops)


# ---------------------------------------------------------------- job hooks (called by actions.run_job)


async def store_created_ad(
    s: AsyncSession, acc_id: int, raw: dict[str, Any], params: dict[str, Any], item: BulkItem
) -> Ad:
    now = now_ts()
    ad_id = int(raw["ad_id"])
    existing = (await s.execute(select(Ad).where(Ad.account_id == acc_id, Ad.ad_id == ad_id))).scalar_one_or_none()
    typ, label = target_summary(raw.get("target"))
    ad = existing or Ad(account_id=acc_id, ad_id=ad_id, remaining_budget=0.0, funded_since_ts=0, empty_since_ts=0)
    if existing is None:
        s.add(ad)
    ad.title = str(raw.get("title") or params.get("title") or "")[:200]
    ad.text = str(raw.get("text") or params.get("text") or "")
    ad.promote_url = str(raw.get("promote_url") or params.get("promote_url") or "")[:300]
    ad.start_code = start_code_of(ad.promote_url)
    ad.tag_id = item.params.get("tag_id") or (await _tag_for_url(s, acc_id, ad.start_code))
    ad.cpm = float(raw.get("cpm") or params.get("cpm") or 0)
    ad.placement = str(raw.get("placement") or params.get("placement") or "")
    ad.status = str(raw.get("status") or "in_review")
    ad.is_paused = bool(raw.get("is_paused"))
    ad.created_date = int(raw.get("created_date") or now)
    ad.target_type, ad.target_label = typ or str(params["target"]["type"]), label
    ad.target = raw.get("target") or {}
    ad.has_photo = bool(raw.get("photo")) or "photo_id" in params
    ad.variant_id = item.params.get("variant_id")
    ad.created_by_app = True
    ad.synced_ts = now
    ad.note = f"جایگزین آگهی {item.ad_id} (عملیات گروهی)"
    budget.observe(
        s, ad, float(raw.get("remaining_budget") or params.get("initial_budget") or 0), now, actor="app", kind="topup"
    )
    return ad


async def _tag_for_url(s: AsyncSession, acc_id: int, code: str) -> int | None:
    if not code:
        return None
    from app.models import ReferralTag

    return (
        await s.execute(select(ReferralTag.id).where(ReferralTag.account_id == acc_id, ReferralTag.start_code == code))
    ).scalar_one_or_none()
