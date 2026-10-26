"""Time windows in the account's own time zone: "today" from local midnight, and per-ad numbers for any
period of the ads list.

Telegram's daily statistics (`AdStat`) are per UTC day. A local "today" (Tehran is UTC+3:30) cannot be
built from them, so the first fast sync of each local day stores every ad's lifetime counters as a
baseline (`AdDayBaseline`); today = current counters − today's baseline. Account totals for today come
from the intraday snapshots the same way.
"""

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.bot_api import zone
from app.models import Account, Ad, AdDayBaseline, AdStat
from app.services.accounts import settings_of

DAY = 86400
BASELINE_KEEP_DAYS = 40
PRESETS = ("today", "yesterday", "7d", "30d", "month", "all", "custom")


def tz_of(acc: Account) -> ZoneInfo:
    return zone(settings_of(acc).get("bot_timezone") or "Asia/Tehran")


def day_bounds(tz: ZoneInfo, now: int) -> tuple[int, int, int]:
    """(local midnight today, local midnight yesterday, the same moment yesterday) as unix seconds.

    "Yesterday up to this hour" makes today's partial numbers comparable with yesterday's.
    """
    local_now = datetime.fromtimestamp(now, tz)
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Arithmetic on zone-aware datetimes is wall-clock time, so this stays right across offset changes.
    prev_midnight = midnight - timedelta(days=1)
    prev_now = local_now - timedelta(days=1)
    return int(midnight.timestamp()), int(prev_midnight.timestamp()), int(prev_now.timestamp())


def local_day(tz: ZoneInfo, ts: int) -> str:
    return datetime.fromtimestamp(ts, tz).date().isoformat()


async def record_baselines(s: AsyncSession, acc: Account, ads: list[Ad], now: int) -> int:
    """Store today's starting counters for every ad that has none yet. An ad created today starts at zero;
    an ad first seen in the middle of the day starts at its current counters (today counts from now)."""
    tz = tz_of(acc)
    today = local_day(tz, now)
    have = {
        int(x)
        for x in (
            await s.execute(
                select(AdDayBaseline.ad_id).where(AdDayBaseline.account_id == acc.id, AdDayBaseline.day == today)
            )
        ).scalars()
    }
    midnight = day_bounds(tz, now)[0]
    added = 0
    for ad in ads:
        if ad.ad_id in have:
            continue
        new_today = bool(ad.created_date) and ad.created_date >= midnight
        s.add(
            AdDayBaseline(
                account_id=acc.id,
                ad_id=ad.ad_id,
                day=today,
                ts=now,
                views=0 if new_today else ad.views,
                clicks=0 if new_today else ad.clicks,
                actions=0 if new_today else ad.actions,
                spent=0.0 if new_today else ad.spent_budget,
            )
        )
        added += 1
    if added and not have:  # once a day: forget old baselines
        cutoff = (date.fromisoformat(today) - timedelta(days=BASELINE_KEEP_DAYS)).isoformat()
        await s.execute(delete(AdDayBaseline).where(AdDayBaseline.account_id == acc.id, AdDayBaseline.day < cutoff))
    return added


async def _baselines(s: AsyncSession, acc: Account, day: str) -> dict[int, tuple[int, int, int, float]]:
    return {
        int(r.ad_id): (r.views, r.clicks, r.actions, r.spent)
        for r in (
            await s.execute(select(AdDayBaseline).where(AdDayBaseline.account_id == acc.id, AdDayBaseline.day == day))
        ).scalars()
    }


def resolve(
    preset: str | None, frm: str | None, to: str | None, tz: ZoneInfo, now: int
) -> tuple[str, str | None, str | None]:
    """Normalize the period: (preset, from_day, to_day). Unknown presets fall back to the last 7 days."""
    preset = preset if preset in PRESETS else "7d"
    today = datetime.fromtimestamp(now, tz).date()
    if preset == "custom":
        try:
            f = date.fromisoformat(frm or "")
            t = date.fromisoformat(to or "") if to else today
        except ValueError:
            return "7d", (today - timedelta(days=6)).isoformat(), today.isoformat()
        if f > t:
            f, t = t, f
        return "custom", f.isoformat(), t.isoformat()
    if preset == "today":
        return preset, today.isoformat(), today.isoformat()
    if preset == "yesterday":
        y = (today - timedelta(days=1)).isoformat()
        return preset, y, y
    if preset == "7d":
        return preset, (today - timedelta(days=6)).isoformat(), today.isoformat()
    if preset == "30d":
        return preset, (today - timedelta(days=29)).isoformat(), today.isoformat()
    if preset == "month":
        return preset, today.replace(day=1).isoformat(), today.isoformat()
    return "all", None, None


async def ad_period_stats(
    s: AsyncSession, acc: Account, preset: str | None, frm: str | None = None, to: str | None = None, *, now: int
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Views, clicks, actions and spend of every ad in the period, and a description of how it was measured.

    today      current counters − today's baseline (local midnight)
    yesterday  today's baseline − yesterday's baseline when both exist, else the UTC day of Telegram's stats
    others     Telegram's daily stats (UTC days) between the two dates
    all        lifetime counters
    """
    tz = tz_of(acc)
    preset, f, t = resolve(preset, frm, to, tz, now)
    meta: dict[str, Any] = {"preset": preset, "from": f, "to": t, "source": "daily", "tz": str(tz)}
    ads = list((await s.execute(select(Ad).where(Ad.account_id == acc.id))).scalars())
    out: dict[int, dict[str, Any]] = {}

    def put(ad_id: int, views: float, clicks: float, actions: float, spent: float) -> None:
        out[ad_id] = {
            "views": max(int(views), 0),
            "clicks": max(int(clicks), 0),
            "actions": max(int(actions), 0),
            "spent": round(max(float(spent), 0.0), 5),
        }

    if preset == "all":
        meta["source"] = "lifetime"
        for a in ads:
            put(a.ad_id, a.views, a.clicks, a.actions, a.spent_budget)
        return out, meta
    today = local_day(tz, now)
    if preset == "today":
        meta["source"] = "baseline"
        meta["since_ts"] = day_bounds(tz, now)[0]
        base = await _baselines(s, acc, today)
        for a in ads:
            b = base.get(a.ad_id)
            if b is None:
                put(a.ad_id, 0, 0, 0, 0)
            else:
                put(a.ad_id, a.views - b[0], a.clicks - b[1], a.actions - b[2], a.spent_budget - b[3])
        return out, meta
    if preset == "yesterday":
        base_today = await _baselines(s, acc, today)
        base_prev = await _baselines(s, acc, f or today)
        if base_today and base_prev:
            meta["source"] = "baseline"
            for a in ads:
                bt, bp = base_today.get(a.ad_id), base_prev.get(a.ad_id)
                if bt and bp:
                    put(a.ad_id, bt[0] - bp[0], bt[1] - bp[1], bt[2] - bp[2], bt[3] - bp[3])
                else:
                    put(a.ad_id, 0, 0, 0, 0)
            return out, meta
    rows = await s.execute(
        select(
            AdStat.ad_id,
            func.sum(AdStat.views),
            func.sum(AdStat.clicks),
            func.sum(AdStat.actions),
            func.sum(AdStat.spent),
        )
        .where(AdStat.account_id == acc.id, AdStat.day >= (f or "0000"), AdStat.day <= (t or "9999"))
        .group_by(AdStat.ad_id)
    )
    found = {int(r[0]): r for r in rows}
    for a in ads:
        r = found.get(a.ad_id)
        if r is None:
            put(a.ad_id, 0, 0, 0, 0)
        else:
            put(a.ad_id, r[1] or 0, r[2] or 0, r[3] or 0, r[4] or 0)
    return out, meta


async def tagged_spent_today(s: AsyncSession, acc: Account, now: int) -> float:
    """Today's spend of the ads that carry a referral tag (for today's cost per referred customer)."""
    base = await _baselines(s, acc, local_day(tz_of(acc), now))
    total = 0.0
    for a in (
        await s.execute(select(Ad).where(Ad.account_id == acc.id, Ad.tag_id.is_not(None), Ad.gone.is_(False)))
    ).scalars():
        b = base.get(a.ad_id)
        if b is not None:
            total += max(a.spent_budget - b[3], 0.0)
    return total
