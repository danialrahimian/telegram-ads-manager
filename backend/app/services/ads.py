"""Creating ads and editing a live ad's text or tracking link."""

import hashlib
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFound, UpstreamError
from app.db.base import now_ts
from app.integrations.telegram_ads import TelegramAdsError, clip_bytes
from app.models import Account, Ad, ReferralTag, TextVariant
from app.services import accounts, actions, events
from app.services import placement as placement_rules
from app.services.accounts import settings_of
from app.services.sync import start_code_of, target_summary

USER_RE = re.compile(r"^@?[A-Za-z0-9_]{4,32}$")
PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]")


def validate_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        raise AppError("متن آگهی خالی است.")
    if len(text) > 160:
        raise AppError(f"متن {len(text)} کاراکتر است؛ سقف تلگرام ۱۶۰ کاراکتر است.")
    if PLACEHOLDER_RE.search(text):
        raise AppError("متن هنوز جای خالی مثل [مدت] دارد. اول آن را با مقدار واقعی پر کنید.")
    return text


def promote_url(acc: Account, tag: ReferralTag | None) -> str:
    if not acc.bot_username:
        raise AppError("نام کاربری ربات را در اتصال ربات (تنظیمات حساب) وارد کنید.")
    base = f"https://t.me/{acc.bot_username}"
    return f"{base}?start={tag.start_code}" if tag else base


async def _tag(s: AsyncSession, acc: Account, tag_id: int | None) -> ReferralTag | None:
    if not tag_id:
        return None
    tag = await s.get(ReferralTag, tag_id)
    if tag is None or tag.account_id != acc.id:
        raise AppError("برچسب معرف انتخاب‌شده پیدا نشد.")
    return tag


async def create(
    s: AsyncSession, acc: Account, data: dict[str, Any], photo: tuple[str, bytes, str] | None = None
) -> Ad:
    st = settings_of(acc)
    kind = data.get("kind")
    if kind not in ("channel", "bot"):
        raise AppError("نوع جایگاه را انتخاب کنید.")
    username = str(data.get("username") or "").strip()
    if not USER_RE.match(username):
        raise AppError("نام کاربری کانال یا ربات معتبر نیست (مثل @examplechannel).")
    username = "@" + username.lstrip("@")
    text = validate_text(str(data.get("text") or ""))
    try:
        cpm = float(data.get("cpm") or 0)
        budget = float(data.get("budget") or 0)
    except (TypeError, ValueError):
        raise AppError("CPM و بودجه باید عدد باشند.") from None
    if cpm <= 0:
        raise AppError("CPM باید بیشتر از صفر باشد.")
    min_budget = float(st["min_initial_budget"])
    if budget + 1e-9 < min_budget:
        raise AppError(
            f"بودجه‌ی اولیه دست‌کم {min_budget:g} {acc.currency or 'TON'} است؛ تلگرام آگهی با بودجه‌ی کمتر نمی‌سازد."
        )
    if budget > st["topup_max_per_ad"] * 5:
        raise AppError(f"بودجه‌ی اولیه بیشتر از {st['topup_max_per_ad'] * 5} مجاز نیست (پنج برابر سقف شارژ).")
    tag = await _tag(s, acc, data.get("tag_id"))
    url = promote_url(acc, tag)
    title = str(data.get("title") or "").strip() or f"{username.lstrip('@')}-{now_ts() % 100000}"

    async with accounts.tg_client(acc) as c:
        try:
            info = await c.account()
            if float(info.get("remaining_budget") or 0) + 1e-9 < budget:
                raise AppError(f"موجودی حساب ({info.get('remaining_budget')}) برای این بودجه کافی نیست.")
            if kind == "channel":
                await c.target_channel(username)
                target, placement = {"type": "channels", "channel_ids": [username]}, "channel_post"
            else:
                await c.target_bot(username)
                target, placement = {"type": "bots", "bot_ids": [username]}, "bot_banner"
        except TelegramAdsError as e:
            raise AppError(f"تلگرام این جایگاه را نپذیرفت: {e.friendly}") from None
        params: dict[str, Any] = {
            "title": clip_bytes(title),
            "text": text,
            "promote_url": url,
            "cpm": cpm,
            "placement": placement,
            "target": target,
            "initial_budget": budget,
            "return_target": True,
        }
        if kind == "bot" and data.get("show_userpic"):
            params["show_userpic"] = True
        try:
            if photo and placement_rules.kind_supports_photo(kind):
                up = await c.upload_photo(*photo)
                params["photo_id"] = up["photo_id"]
            # Key tied to the request contents: a retry after a lost response cannot create a second ad.
            fp = json.dumps([acc.id, title, text, url, username, cpm, budget], ensure_ascii=False)
            key = "create-" + hashlib.sha256(fp.encode()).hexdigest()[:32]
            raw = await c.create_ad(key, **params)
            raw, _ = await actions.submit_if_ready(c, int(raw.get("ad_id") or 0), raw)
        except TelegramAdsError as e:
            raise UpstreamError(f"تلگرام آگهی را نساخت: {e.friendly}") from None

    variant_id = None
    if str(data.get("variant_id") or "").isdigit():
        v = await s.get(TextVariant, int(data["variant_id"]))
        if v and v.account_id == acc.id and v.text.strip() == text:  # only an unedited text counts for A/B
            variant_id = v.id
    typ, label = target_summary(raw.get("target"))
    existing = (
        await s.execute(select(Ad).where(Ad.account_id == acc.id, Ad.ad_id == int(raw["ad_id"])))
    ).scalar_one_or_none()
    if existing is not None:  # a sync picked the new ad up first
        await s.delete(existing)
        await s.flush()
    ad = Ad(
        account_id=acc.id,
        ad_id=int(raw["ad_id"]),
        title=str(raw.get("title") or title),
        text=str(raw.get("text") or text),
        promote_url=str(raw.get("promote_url") or url),
        start_code=start_code_of(url),
        cpm=float(raw.get("cpm") or cpm),
        placement=placement,
        status=str(raw.get("status") or "in_review"),
        is_paused=bool(raw.get("is_paused")),
        remaining_budget=float(raw.get("remaining_budget") or budget),
        created_date=int(raw.get("created_date") or now_ts()),
        target_type=typ or target["type"],
        target_label=label or username,
        target=raw.get("target") or {},
        has_photo="photo_id" in params,
        tag_id=tag.id if tag else None,
        variant_id=variant_id,
        note=str(data.get("note") or "")[:500],
        created_by_app=True,
        synced_ts=now_ts(),
        funded_since_ts=now_ts(),
        empty_since_ts=0,
    )
    s.add(ad)
    events.log(
        s,
        "create_ad",
        account_id=acc.id,
        actor="user",
        ad_id=ad.ad_id,
        detail=f"{username} بودجه {budget}" + (f" برچسب {tag.name}" if tag else ""),
    )
    replace = data.get("replace_ad_id")
    if replace:
        actions.stop_and_withdraw(s, acc, int(replace))
    return ad


async def edit(s: AsyncSession, acc: Account, ad_id: int, data: dict[str, Any]) -> tuple[Ad, bool, str]:
    """Change note/variant locally, or text/tracking link on Telegram (which sends the ad back to review).

    Returns the ad, whether Telegram was changed, and a warning when Telegram kept the ad out of review.
    """
    review_note = ""
    ad = (await s.execute(select(Ad).where(Ad.account_id == acc.id, Ad.ad_id == ad_id))).scalar_one_or_none()
    if ad is None:
        raise NotFound("این آگهی پیدا نشد.")
    remote: dict[str, Any] = {}
    if "note" in data and data["note"] is not None:
        ad.note = str(data["note"])[:500]
    if "variant_id" in data:
        vid = data["variant_id"]
        if vid:
            v = await s.get(TextVariant, int(vid))
            if v is None or v.account_id != acc.id:
                raise AppError("متن انتخاب‌شده پیدا نشد.")
        ad.variant_id = int(vid) if vid else None
    if data.get("text") is not None:
        text = validate_text(str(data["text"]))
        if text != ad.text:
            remote["text"] = text
    if "tag_id" in data:
        tag = await _tag(s, acc, data["tag_id"])
        url = promote_url(acc, tag)
        if url != ad.promote_url:
            remote["promote_url"] = url
    if remote:
        if acc.automation_paused:
            raise AppError("اقدام‌های این حساب متوقف است.", code="automation_paused")
        from app.services import bulk

        cap = await bulk.capabilities(s)
        if cap["edit_format"] == "none":
            raise AppError(bulk.EDIT_NOT_APPLIED)
        try:
            async with accounts.tg_client(acc) as c:
                raw = await c.edit_ad(ad_id, fmt=bulk.edit_format(cap), **remote)
                raw, review_note = await actions.submit_if_ready(c, ad_id, raw)
        except TelegramAdsError as e:
            raise UpstreamError(f"تلگرام تغییر را نپذیرفت: {e.friendly}") from None
        missing = actions.unapplied(remote, raw)
        if missing and raw.get("status") not in actions.IN_REVIEW:
            raise UpstreamError(actions.not_applied_message(missing, raw))
        if missing:
            review_note = review_note or actions.pending_message(missing)
        ad.text = str(raw["text"]) if "text" in raw else str(remote.get("text") or ad.text)
        ad.promote_url = (
            str(raw["promote_url"]) if "promote_url" in raw else str(remote.get("promote_url") or ad.promote_url)
        )
        ad.start_code = start_code_of(ad.promote_url)
        tags = {
            t.start_code: t.id
            for t in (await s.execute(select(ReferralTag).where(ReferralTag.account_id == acc.id))).scalars()
        }
        ad.tag_id = tags.get(ad.start_code) if ad.start_code else None
        ad.status = str(raw.get("status") or ad.status)
        if "text" in remote and ad.variant_id:
            v = await s.get(TextVariant, ad.variant_id)
            if v is None or v.text.strip() != ad.text:
                ad.variant_id = None
        events.log(s, "edit_ad", account_id=acc.id, actor="user", ad_id=ad_id, detail=", ".join(sorted(remote)))
    return ad, bool(remote), review_note
