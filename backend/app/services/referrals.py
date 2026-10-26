"""Referral tags: referrer accounts in the sales bot whose invite links are used as ad links."""

import re
from collections import defaultdict
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, Conflict, NotFound
from app.db.base import now_ts
from app.models import Account, Ad, BotUser, ReferralTag
from app.services import accounts, events
from app.services.accounts import settings_of

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ID_RE = re.compile(r"^\d{3,20}$")


def _validate(name: str, referrer_id: str, start_code: str) -> tuple[str, str, str]:
    name, referrer_id, start_code = name.strip(), referrer_id.strip(), start_code.strip()
    if not name:
        raise AppError("نام برچسب را وارد کنید.")
    if not ID_RE.match(referrer_id):
        raise AppError("شناسه‌ی عددی تلگرام حساب معرف را وارد کنید (فقط عدد).")
    if not start_code:
        start_code = referrer_id
    if not CODE_RE.match(start_code):
        raise AppError("کد start فقط می‌تواند حروف انگلیسی، عدد، _ و - داشته باشد (حداکثر ۶۴ کاراکتر).")
    return name[:80], referrer_id, start_code


async def get(s: AsyncSession, acc: Account, tag_id: int) -> ReferralTag:
    tag = await s.get(ReferralTag, tag_id)
    if tag is None or tag.account_id != acc.id:
        raise NotFound("این برچسب پیدا نشد.")
    return tag


async def _link_ads(s: AsyncSession, acc: Account, tag: ReferralTag) -> None:
    await s.execute(update(Ad).where(Ad.account_id == acc.id, Ad.start_code == tag.start_code).values(tag_id=tag.id))


async def create(
    s: AsyncSession, acc: Account, name: str, referrer_id: str, start_code: str, note: str = ""
) -> ReferralTag:
    name, referrer_id, start_code = _validate(name, referrer_id, start_code)
    dup = (
        await s.execute(
            select(ReferralTag.id).where(ReferralTag.account_id == acc.id, ReferralTag.start_code == start_code)
        )
    ).first()
    if dup:
        raise Conflict("برچسبی با همین کد start وجود دارد.")
    tag = ReferralTag(
        account_id=acc.id,
        name=name,
        referrer_id=referrer_id,
        start_code=start_code,
        note=note.strip()[:500],
        created_ts=now_ts(),
    )
    s.add(tag)
    await s.flush()
    await _link_ads(s, acc, tag)
    events.log(s, "tag_created", account_id=acc.id, actor="user", detail=f"{name} ({start_code})")
    return tag


async def update_tag(
    s: AsyncSession,
    acc: Account,
    tag_id: int,
    *,
    name: str | None,
    referrer_id: str | None,
    start_code: str | None,
    note: str | None,
) -> ReferralTag:
    tag = await get(s, acc, tag_id)
    n, r, c = _validate(
        name if name is not None else tag.name,
        referrer_id if referrer_id is not None else tag.referrer_id,
        start_code if start_code is not None else tag.start_code,
    )
    if c != tag.start_code:
        dup = (
            await s.execute(
                select(ReferralTag.id).where(
                    ReferralTag.account_id == acc.id, ReferralTag.start_code == c, ReferralTag.id != tag.id
                )
            )
        ).first()
        if dup:
            raise Conflict("برچسبی با همین کد start وجود دارد.")
        await s.execute(update(Ad).where(Ad.account_id == acc.id, Ad.tag_id == tag.id).values(tag_id=None))
    if r != tag.referrer_id:
        await s.execute(
            update(BotUser).where(BotUser.account_id == acc.id, BotUser.tag_id == tag.id).values(tag_id=None)
        )
        tag.referred_count = 0
    tag.name, tag.referrer_id, tag.start_code = n, r, c
    if note is not None:
        tag.note = note.strip()[:500]
    await _link_ads(s, acc, tag)
    return tag


async def delete(s: AsyncSession, acc: Account, tag_id: int) -> None:
    tag = await get(s, acc, tag_id)
    await s.delete(tag)
    events.log(s, "tag_deleted", account_id=acc.id, actor="user", detail=tag.name)


# ---------------------------------------------------------------- referrer pool (bulk referral links)


async def tags_by_referrer(s: AsyncSession, acc: Account) -> dict[str, ReferralTag]:
    return {
        t.referrer_id: t
        for t in (await s.execute(select(ReferralTag).where(ReferralTag.account_id == acc.id))).scalars()
    }


async def pool_status(s: AsyncSession, acc: Account) -> list[dict[str, Any]]:
    """Each referrer of the pool: its tag (if any), how many live ads use it, and the last check."""
    pool = [str(x) for x in settings_of(acc).get("referrer_pool") or []]
    tags = await tags_by_referrer(s, acc)
    use: dict[int, int] = defaultdict(int)
    for (tag_id,) in await s.execute(
        select(Ad.tag_id).where(
            Ad.account_id == acc.id, Ad.gone.is_(False), Ad.archived.is_(False), Ad.tag_id.is_not(None)
        )
    ):
        use[int(tag_id or 0)] += 1
    checks = (acc.sync_state or {}).get("pool_checks") or {}
    out = []
    for rid in pool:
        tag = tags.get(rid)
        ads = use.get(tag.id, 0) if tag else 0
        out.append(
            {
                "referrer_id": rid,
                "tag_id": tag.id if tag else None,
                "tag_name": tag.name if tag else None,
                "ads": ads,
                "free": ads == 0,
                "check": checks.get(rid),
            }
        )
    return out


async def check_pool(acc: Account) -> dict[str, dict[str, Any]]:
    """Confirm each pool referrer exists in the bot (users → user) and count who it already invited."""
    pool = [str(x) for x in settings_of(acc).get("referrer_pool") or []]
    out: dict[str, dict[str, Any]] = {}
    for rid in pool:
        res = await accounts.lookup_referrer(acc, rid)
        out[rid] = {
            "exists": bool(res.get("ok")),
            "invited": res.get("referred"),
            "error": res.get("error") or res.get("warning") or "",
            "ts": now_ts(),
        }
    return out


def _group_key(r: dict[str, Any], group_by: str) -> str:
    if group_by == "cpm":
        return f"CPM {r['cpm']:g}"
    if group_by == "placement":
        return {"channel_post": "کانال", "bot_banner": "ربات"}.get(r["placement"], r["placement"] or "—")
    return r["target_label"] or f"#{r['ad_id']}"


def _join_labels(labels: list[str]) -> str:
    head = "، ".join(labels[:2])
    return head + (f" و {len(labels) - 2} گروه دیگر" if len(labels) > 2 else "")


def assign(
    rows: list[dict[str, Any]], pool: list[dict[str, Any]], group_by: str = "target"
) -> tuple[list[dict[str, Any]], list[str]]:
    """Give each ad a free referrer; with fewer referrers than ads, group the ads (by target channel/bot,
    CPM or placement) and give each group one referrer, balancing the group sizes.

    Returns (assignments [{referrer_id, tag_id, label, ad_ids}], warnings).
    """
    free = [p for p in pool if p["free"] and (p.get("check") or {}).get("exists", True)]
    warnings: list[str] = []
    if not rows:
        return [], warnings
    if not free:
        return [], ["معرف آزادی در استخر نیست؛ در تنظیمات حساب، شناسه‌ی حساب‌های معرفِ خودتان را اضافه کنید."]
    for p in free:
        invited = (p.get("check") or {}).get("invited")
        if invited:
            warnings.append(
                f"معرف {p['referrer_id']} از قبل {invited} دعوت دارد؛ آمار آگهی‌هایش با دعوت‌های قبلی مخلوط می‌شود."
            )
    if len(free) >= len(rows):
        return [
            {
                "referrer_id": p["referrer_id"],
                "tag_id": p["tag_id"],
                "label": _group_key(r, "target"),
                "ad_ids": [r["ad_id"]],
            }
            for p, r in zip(free, rows, strict=False)
        ], warnings
    groups: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        groups[_group_key(r, group_by)].append(r["ad_id"])
    buckets: list[dict[str, Any]] = [
        {"referrer_id": p["referrer_id"], "tag_id": p["tag_id"], "labels": [], "ad_ids": []} for p in free
    ]
    for label, ids in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        b = min(buckets, key=lambda x: len(x["ad_ids"]))
        b["labels"].append(label)
        b["ad_ids"].extend(ids)
    out = [
        {
            "referrer_id": b["referrer_id"],
            "tag_id": b["tag_id"],
            "label": _join_labels(b["labels"]),
            "ad_ids": b["ad_ids"],
        }
        for b in buckets
        if b["ad_ids"]
    ]
    warnings.append(
        f"{len(free)} معرف آزاد برای {len(rows)} آگهی هست؛ آگهی‌ها گروه‌بندی شدند و آگهی‌های هر گروه برچسب مشترک می‌گیرند. "
        "فروش برچسب مشترک بین آگهی‌هایش تقسیم نمی‌شود و فقط برای کل گروه دیده می‌شود."
    )
    return out, warnings


async def ensure_tag(s: AsyncSession, acc: Account, referrer_id: str, label: str) -> ReferralTag:
    """The tag of a pool referrer, created on first use. Its start code is the referrer's numeric id:
    the bot attributes a new user only when `start` is the id of an existing user."""
    tag = (
        await s.execute(
            select(ReferralTag).where(ReferralTag.account_id == acc.id, ReferralTag.referrer_id == referrer_id)
        )
    ).scalar_one_or_none()
    if tag is not None:
        return tag
    return await create(s, acc, f"رفرال {label}"[:80], referrer_id, referrer_id, note="ساخته‌شده با عملیات گروهی")


async def resolve_shared(
    s: AsyncSession, acc: Account, referrer_id: Any, tag_id: Any, *, check: bool = True
) -> dict[str, Any]:
    """The one link given to every selected ad: an existing tag, or a referrer id (from the pool or typed).

    Unlike the pool assignment, a referrer or tag already used by live ads is fine here.
    """
    if tag_id:
        tag = await s.get(ReferralTag, int(tag_id))
        if tag is None or tag.account_id != acc.id:
            return {"error": "برچسب انتخاب‌شده پیدا نشد."}
        return {
            "referrer_id": tag.referrer_id,
            "start_code": tag.start_code,
            "tag_id": tag.id,
            "label": tag.name,
        }
    rid = str(referrer_id or "").strip()
    if not rid:
        return {"error": "برای لینک مشترک یک معرف یا برچسب انتخاب کنید."}
    if not ID_RE.match(rid):
        return {"error": "شناسه‌ی معرف باید عددی باشد."}
    existing = (await tags_by_referrer(s, acc)).get(rid)
    if existing is not None:
        return {
            "referrer_id": rid,
            "start_code": existing.start_code,
            "tag_id": existing.id,
            "label": existing.name,
        }
    out: dict[str, Any] = {"referrer_id": rid, "start_code": rid, "tag_id": None, "label": f"لینک مشترک {rid}"}
    if check:
        res = await accounts.lookup_referrer(acc, rid)
        if not res.get("ok"):
            return {"error": f"شناسه‌ی {rid} در ربات کاربر نیست یا بررسی نشد: {res.get('error') or ''}".strip()}
        if res.get("referred"):
            out["warning"] = (
                f"معرف {rid} از قبل {res['referred']} دعوت دارد؛ آمار این آگهی‌ها با دعوت‌های قبلی مخلوط می‌شود."
            )
    return out
