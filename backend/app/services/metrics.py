"""Read-side numbers for the dashboard. All money is in the account currency unless named *_toman."""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import now_ts
from app.integrations.bot_api import zone
from app.models import Account, AccountSnapshot, Ad, AdStat, BotTransaction, BotUser, ReferralTag
from app.services import budget
from app.services.accounts import settings_of

RUNNING = ("active",)


def today_utc() -> date:
    return datetime.now(UTC).date()


def _ratio(a: float, b: float) -> float | None:
    return (a / b) if b else None


async def _window(s: AsyncSession, account_id: int, since: str, until: str = "9999-12-31") -> dict[int, dict]:
    rows = await s.execute(
        select(
            AdStat.ad_id,
            func.sum(AdStat.views),
            func.sum(AdStat.clicks),
            func.sum(AdStat.actions),
            func.sum(AdStat.spent),
        )
        .where(AdStat.account_id == account_id, AdStat.day >= since, AdStat.day <= until)
        .group_by(AdStat.ad_id)
    )
    return {
        int(r[0]): {
            "views": int(r[1] or 0),
            "clicks": int(r[2] or 0),
            "starts": int(r[3] or 0),
            "spent": float(r[4] or 0),
        }
        for r in rows
    }


def _purchase_filter(st: dict[str, Any]) -> Any:
    types = st.get("purchase_types") or ["buy"]
    return BotTransaction.type.in_(types)


async def tag_sales(s: AsyncSession, acc: Account, since_ts: int = 0) -> dict[int, dict[str, Any]]:
    """Per referral tag: users who came through it, buyers, purchases and revenue (Toman)."""
    st = settings_of(acc)
    users = await s.execute(
        select(BotUser.tag_id, func.count(BotUser.id))
        .where(BotUser.account_id == acc.id, BotUser.tag_id.is_not(None))
        .group_by(BotUser.tag_id)
    )
    out: dict[int, dict[str, Any]] = defaultdict(lambda: {"users": 0, "buyers": 0, "purchases": 0, "revenue": 0.0})
    for tag_id, n in users:
        out[int(tag_id or 0)]["users"] = int(n)
    q = (
        select(
            BotUser.tag_id,
            func.count(BotTransaction.id),
            func.count(func.distinct(BotTransaction.user_id)),
            func.sum(func.abs(BotTransaction.amount)),
        )
        .join(BotUser, and_(BotUser.account_id == BotTransaction.account_id, BotUser.user_id == BotTransaction.user_id))
        .where(
            BotTransaction.account_id == acc.id,
            BotUser.tag_id.is_not(None),
            _purchase_filter(st),
            BotTransaction.created_ts >= since_ts,
        )
        .group_by(BotUser.tag_id)
    )
    for tag_id, purchases, buyers, revenue in await s.execute(q):
        d = out[int(tag_id)]
        d["purchases"], d["buyers"], d["revenue"] = int(purchases), int(buyers), float(revenue or 0)
    return dict(out)


LOOKBACK_DAYS = 120


async def _daily_by_ad(
    s: AsyncSession, account_id: int, since: str
) -> dict[int, dict[str, tuple[int, int, int, float]]]:
    out: dict[int, dict[str, tuple[int, int, int, float]]] = defaultdict(dict)
    for ad_id, day, views, clicks, actions, spent in await s.execute(
        select(AdStat.ad_id, AdStat.day, AdStat.views, AdStat.clicks, AdStat.actions, AdStat.spent).where(
            AdStat.account_id == account_id, AdStat.day >= since
        )
    ):
        out[int(ad_id)][str(day)] = (int(views or 0), int(clicks or 0), int(actions or 0), float(spent or 0))
    return out


def _funded_metrics(
    a: Ad,
    daily: dict[str, tuple[int, int, int, float]],
    events: list[tuple[int, str, float]],
    day_views: dict[str, int],
    st: dict[str, Any],
    today: date,
    now: int,
) -> dict[str, Any]:
    """Numbers measured on funded days only: the last 28 days with budget, the current funded stretch,
    days without budget and the current run of days without a single view."""
    spend_days = sorted(d for d, v in daily.items() if v[3] > 0)
    funded = budget.funded_days(
        events,
        remaining=a.remaining_budget,
        created_ts=a.created_date,
        spend_days=spend_days,
        today=today,
        lookback=LOOKBACK_DAYS,
    )
    last28 = sorted(funded, reverse=True)[:28]
    agg = [0, 0, 0, 0.0]
    for d in last28:
        v = daily.get(d)
        if v:
            for i in range(4):
                agg[i] += v[i]
    out: dict[str, Any] = {
        "funded_days_28": len(last28),
        "views_f28": agg[0],
        "clicks_f28": agg[1],
        "starts_f28": agg[2],
        "spent_f28": agg[3],
        "cps_f28": _ratio(agg[3], agg[2]),
        "funded_since_ts": a.funded_since_ts,
        "empty_since_ts": a.empty_since_ts,
        "empty_days": None,
        "window_days": 0.0,
        "spent_window": 0.0,
        "zero_view_days": 0,
        "others_views_in_streak": 0,
    }
    if budget.has_budget(a.remaining_budget):
        start = max(now - 28 * 86400, a.funded_since_ts or a.created_date or now)
        out["window_days"] = round(max(0, now - start) / 86400, 2)
        first = datetime.fromtimestamp(start, UTC).date().isoformat()
        out["spent_window"] = sum(v[3] for d, v in daily.items() if d >= first)
    else:
        since = (
            datetime.fromtimestamp(a.empty_since_ts, UTC).date()
            if a.empty_since_ts
            else (date.fromisoformat(spend_days[-1]) if spend_days else None)
        )
        out["empty_days"] = (today - since).days if since else None
    # Run of whole days (yesterday backwards) with budget and no views at all.
    streak, others = 0, 0
    cur = today - timedelta(days=1)
    for _ in range(int(st.get("replace_zero_view_days") or 5) + 30):
        key = cur.isoformat()
        own = daily.get(key, (0, 0, 0, 0.0))[0]
        if own or key not in funded:
            break
        streak += 1
        others += day_views.get(key, 0)
        cur -= timedelta(days=1)
    out["zero_view_days"], out["others_views_in_streak"] = streak, others
    return out


async def ad_rows(
    s: AsyncSession, acc: Account, since_day: str | None = None, *, archived: bool | None = False
) -> list[dict[str, Any]]:
    """One row per ad. archived=False hides ads retired by a replacement, True shows only them, None all."""
    t = today_utc()
    now = now_ts()
    st = settings_of(acc)
    lookback = (t - timedelta(days=LOOKBACK_DAYS - 1)).isoformat()
    daily_all = await _daily_by_ad(s, acc.id, lookback)
    events = await budget.events_by_ad(s, acc.id, now - LOOKBACK_DAYS * 86400)
    day_views: dict[str, int] = defaultdict(int)
    for per_day in daily_all.values():
        for d, v in per_day.items():
            day_views[d] += v[0]
    d7 = (t - timedelta(days=6)).isoformat()
    d28 = (t - timedelta(days=27)).isoformat()
    w7, w28 = await _window(s, acc.id, d7), await _window(s, acc.id, d28)
    ws = await _window(s, acc.id, since_day) if since_day else {}
    first = {
        int(r[0]): (r[1], r[2])
        for r in await s.execute(
            select(AdStat.ad_id, func.min(AdStat.day), func.max(case((AdStat.spent > 0, AdStat.day), else_=None)))
            .where(AdStat.account_id == acc.id)
            .group_by(AdStat.ad_id)
        )
    }
    ads = list(
        (
            await s.execute(
                select(Ad)
                .where(
                    Ad.account_id == acc.id,
                    Ad.gone.is_(False),
                    *(() if archived is None else (Ad.archived.is_(archived),)),
                )
                .order_by(Ad.ad_id.desc())
            )
        ).scalars()
    )
    tags = {
        tg.id: tg for tg in (await s.execute(select(ReferralTag).where(ReferralTag.account_id == acc.id))).scalars()
    }
    tag_use: dict[int, int] = defaultdict(int)
    for a in ads:
        if a.tag_id:
            tag_use[a.tag_id] += 1
    sales = await tag_sales(s, acc)
    empty = {"views": 0, "clicks": 0, "starts": 0, "spent": 0.0}
    out = []
    for a in ads:
        created = datetime.fromtimestamp(a.created_date, UTC).date() if a.created_date else None
        sale = sales.get(a.tag_id or -1)
        shared = bool(a.tag_id and tag_use[a.tag_id] > 1)
        r: dict[str, Any] = {
            "ad_id": a.ad_id,
            "title": a.title,
            "text": a.text,
            "promote_url": a.promote_url,
            "start_code": a.start_code,
            "cpm": a.cpm,
            "placement": a.placement,
            "status": a.status,
            "is_paused": a.is_paused,
            "has_stats": a.stats_ts > 0,
            "running": a.status in RUNNING and not a.is_paused,
            "views": a.views,
            "clicks": a.clicks,
            "starts": a.actions,
            "action_type": a.action_type,
            "spent": a.spent_budget,
            "remaining": a.remaining_budget,
            "daily_budget_limit": a.daily_budget_limit,
            "created_date": a.created_date,
            "age_days": (t - created).days if created else None,
            "target_type": a.target_type,
            "target_label": a.target_label,
            "has_photo": a.has_photo,
            "decline_reason": a.decline_reason,
            "note": a.note,
            "variant_id": a.variant_id,
            "tag_id": a.tag_id,
            "tag_name": tags[a.tag_id].name if a.tag_id in tags else None,
            "tag_kind": tags[a.tag_id].kind if a.tag_id in tags else None,
            "tag_shared": shared,
            "first_day": first.get(a.ad_id, (None, None))[0],
            "last_spend_day": first.get(a.ad_id, (None, None))[1],
            "cps": _ratio(a.spent_budget, a.actions),
            "ctr": _ratio(a.clicks, a.views),
            "start_rate": _ratio(a.actions, a.clicks),
            "buyers": sale["buyers"] if sale and not shared else None,
            "purchases": sale["purchases"] if sale and not shared else None,
            "revenue": sale["revenue"] if sale and not shared else None,
        }
        r["cpp"] = _ratio(a.spent_budget, r["buyers"]) if r["buyers"] else None
        for key, w in (("7", w7), ("28", w28), ("since", ws)):
            x = w.get(a.ad_id, empty)
            r[f"spent_{key}"], r[f"starts_{key}"] = x["spent"], x["starts"]
            r[f"clicks_{key}"], r[f"views_{key}"] = x["clicks"], x["views"]
            r[f"cps_{key}"] = _ratio(x["spent"], x["starts"])
        own = daily_all.get(a.ad_id, {})
        own_views = {d: v[0] for d, v in own.items()}
        others = {d: n - own_views.get(d, 0) for d, n in day_views.items()}
        r.update(_funded_metrics(a, own, events.get(a.ad_id, []), others, st, t, now))
        r["archived"] = a.archived
        r["created_by_app"] = a.created_by_app
        out.append(r)
    return out


async def early_cps(
    s: AsyncSession, account_id: int, ad_id: int, first_day: str | None, days: int = 14
) -> float | None:
    if not first_day:
        return None
    end = (date.fromisoformat(first_day) + timedelta(days=days - 1)).isoformat()
    r = (
        await s.execute(
            select(func.sum(AdStat.spent), func.sum(AdStat.actions)).where(
                AdStat.account_id == account_id, AdStat.ad_id == ad_id, AdStat.day >= first_day, AdStat.day <= end
            )
        )
    ).one()
    return _ratio(float(r[0] or 0), float(r[1] or 0))


def user_growth(counts: list[int]) -> int:
    """New bot users from a series of `count_user` readings.

    Sums the increases between consecutive readings. A drop, or a jump far too big for one sync interval,
    means the counter changed meaning (another bot or key was connected), so that step is ignored.
    """
    total = 0
    for prev, cur in pairwise(counts):
        step = cur - prev
        if 0 < step <= max(500, prev // 2):
            total += step
    return total


async def _bot_new_users(s: AsyncSession, account_id: int, since_ts: int, until_ts: int | None = None) -> int | None:
    q = (
        select(AccountSnapshot.bot_users)
        .where(
            AccountSnapshot.account_id == account_id,
            AccountSnapshot.ts >= since_ts,
            AccountSnapshot.bot_users.is_not(None),
        )
        .order_by(AccountSnapshot.ts)
    )
    if until_ts:
        q = q.where(AccountSnapshot.ts < until_ts)
    counts = [int(x) for x in (await s.execute(q)).scalars() if x is not None]
    return user_growth(counts) if counts else None


async def _sales_window(s: AsyncSession, acc: Account, since_ts: int, until_ts: int) -> dict[str, Any]:
    st = settings_of(acc)
    purchases, buyers, revenue = (
        await s.execute(
            select(
                func.count(BotTransaction.id),
                func.count(func.distinct(BotTransaction.user_id)),
                func.sum(func.abs(BotTransaction.amount)),
            ).where(
                BotTransaction.account_id == acc.id,
                _purchase_filter(st),
                BotTransaction.created_ts >= since_ts,
                BotTransaction.created_ts < until_ts,
            )
        )
    ).one()
    return {"purchases": int(purchases or 0), "buyers": int(buyers or 0), "revenue": float(revenue or 0)}


async def _snapshot_at(s: AsyncSession, account_id: int, ts: int, *, after: int | None = None) -> Any:
    """The last snapshot at or before `ts` (or, failing that, the first one after `after`)."""
    row = (
        await s.execute(
            select(
                AccountSnapshot.ts,
                AccountSnapshot.spent_budget,
                AccountSnapshot.views,
                AccountSnapshot.clicks,
                AccountSnapshot.actions,
            )
            .where(AccountSnapshot.account_id == account_id, AccountSnapshot.ts <= ts)
            .order_by(AccountSnapshot.ts.desc())
            .limit(1)
        )
    ).first()
    if row is None and after is not None:
        row = (
            await s.execute(
                select(
                    AccountSnapshot.ts,
                    AccountSnapshot.spent_budget,
                    AccountSnapshot.views,
                    AccountSnapshot.clicks,
                    AccountSnapshot.actions,
                )
                .where(AccountSnapshot.account_id == account_id, AccountSnapshot.ts >= after, AccountSnapshot.ts <= ts)
                .order_by(AccountSnapshot.ts)
                .limit(1)
            )
        ).first()
    return row


async def _snap_delta(s: AsyncSession, account_id: int, start: int, end: int) -> dict[str, Any]:
    base = await _snapshot_at(s, account_id, start, after=start)
    last = await _snapshot_at(s, account_id, end)
    if base is None or last is None or last[0] < base[0]:
        return {"spent": 0.0, "views": 0, "clicks": 0, "starts": 0}
    return {
        "spent": round(max(last[1] - base[1], 0.0), 5),
        "views": max(last[2] - base[2], 0),
        "clicks": max(last[3] - base[3], 0),
        "starts": max(last[4] - base[4], 0),
    }


async def today_window(s: AsyncSession, acc: Account, now: int) -> dict[str, Any]:
    """Today from local midnight (the bot's time zone), compared with yesterday up to the same hour.

    Spend, views, clicks and starts come from the intraday account snapshots; sales from the bot's
    transactions; new bot users from the growth of its user count.
    """
    from app.services import periods

    tz = periods.tz_of(acc)
    midnight, prev_midnight, prev_now = periods.day_bounds(tz, now)
    cur = await _snap_delta(s, acc.id, midnight, now)
    prev = await _snap_delta(s, acc.id, prev_midnight, prev_now)
    sales = await _sales_window(s, acc, midnight, now + 1)
    prev_sales = await _sales_window(s, acc, prev_midnight, prev_now)
    return {
        **cur,
        "cps": _ratio(cur["spent"], cur["starts"]),
        "prev_cps": _ratio(prev["spent"], prev["starts"]),
        "prev_spent": prev["spent"],
        "prev_starts": prev["starts"],
        "ctr": _ratio(cur["clicks"], cur["views"]),
        "start_rate": _ratio(cur["starts"], cur["clicks"]),
        **sales,
        "prev_revenue": prev_sales["revenue"],
        "prev_buyers": prev_sales["buyers"],
        "new_users": await _bot_new_users(s, acc.id, midnight),
        "prev_new_users": await _bot_new_users(s, acc.id, prev_midnight, prev_now),
        "since_ts": midnight,
        "compare_until_ts": prev_now,
        "tz": str(tz),
    }


async def summary(s: AsyncSession, acc: Account) -> dict[str, Any]:
    from app.services import periods

    t = today_utc()
    st = settings_of(acc)
    now = now_ts()
    out: dict[str, Any] = {"today": await today_window(s, acc, now)}
    for key, days in (("7", 7), ("28", 28)):
        since = (t - timedelta(days=days - 1)).isoformat()
        prev_since = (t - timedelta(days=2 * days - 1)).isoformat()
        prev_until = (t - timedelta(days=days)).isoformat()
        cur = (
            await s.execute(
                select(
                    func.sum(AdStat.views), func.sum(AdStat.clicks), func.sum(AdStat.actions), func.sum(AdStat.spent)
                ).where(AdStat.account_id == acc.id, AdStat.day >= since)
            )
        ).one()
        prv = (
            await s.execute(
                select(func.sum(AdStat.actions), func.sum(AdStat.spent)).where(
                    AdStat.account_id == acc.id, AdStat.day >= prev_since, AdStat.day <= prev_until
                )
            )
        ).one()
        views, clicks, starts, spent = (int(cur[0] or 0), int(cur[1] or 0), int(cur[2] or 0), float(cur[3] or 0))
        p_starts, p_spent = int(prv[0] or 0), float(prv[1] or 0)
        sales = await _sales_window(s, acc, now - days * 86400, now + 1)
        prev_sales = await _sales_window(s, acc, now - 2 * days * 86400, now - days * 86400)
        out[key] = {
            "views": views,
            "clicks": clicks,
            "starts": starts,
            "spent": spent,
            "cps": _ratio(spent, starts),
            "prev_cps": _ratio(p_spent, p_starts),
            "prev_spent": p_spent,
            "prev_starts": p_starts,
            "ctr": _ratio(clicks, views),
            "start_rate": _ratio(starts, clicks),
            **sales,
            "prev_revenue": prev_sales["revenue"],
            "prev_buyers": prev_sales["buyers"],
            "new_users": await _bot_new_users(s, acc.id, now - days * 86400),
        }
    tagged = await tag_sales(s, acc, now - 28 * 86400)
    tagged_ad_spend = float(
        (
            await s.execute(
                select(func.sum(AdStat.spent))
                .join(Ad, and_(Ad.account_id == AdStat.account_id, Ad.ad_id == AdStat.ad_id))
                .where(
                    AdStat.account_id == acc.id,
                    Ad.tag_id.is_not(None),
                    AdStat.day >= (t - timedelta(days=27)).isoformat(),
                )
            )
        ).scalar_one()
        or 0
    )
    attr_buyers = sum(v["buyers"] for v in tagged.values())
    attr_revenue = sum(v["revenue"] for v in tagged.values())
    out["attributed_28"] = {
        "buyers": attr_buyers,
        "purchases": sum(v["purchases"] for v in tagged.values()),
        "revenue": attr_revenue,
        "users": sum(v["users"] for v in tagged.values()),
        "ad_spend": tagged_ad_spend,
        "cpp": _ratio(tagged_ad_spend, attr_buyers),
    }
    tagged_today = await tag_sales(s, acc, out["today"]["since_ts"])
    spend_today = await periods.tagged_spent_today(s, acc, now)
    buyers_today = sum(v["buyers"] for v in tagged_today.values())
    out["attributed_today"] = {
        "buyers": buyers_today,
        "purchases": sum(v["purchases"] for v in tagged_today.values()),
        "revenue": sum(v["revenue"] for v in tagged_today.values()),
        "users": sum(v["users"] for v in tagged_today.values()),
        "ad_spend": round(spend_today, 5),
        "cpp": _ratio(spend_today, buyers_today),
    }
    rate = float(st.get("toman_per_unit") or 0)
    out["roas_28"] = _ratio(out["28"]["revenue"], out["28"]["spent"] * rate) if rate else None
    counts = (
        await s.execute(
            select(
                func.sum(case((and_(Ad.status == "active", Ad.is_paused.is_(False)), 1), else_=0)),
                func.sum(case((Ad.status.in_(("in_review", "ready_for_review")), 1), else_=0)),
                func.sum(case((Ad.status == "declined", 1), else_=0)),
                func.count(Ad.id),
            ).where(Ad.account_id == acc.id, Ad.gone.is_(False), Ad.archived.is_(False))
        )
    ).one()
    out["counts"] = {
        "running": int(counts[0] or 0),
        "review": int(counts[1] or 0),
        "declined": int(counts[2] or 0),
        "total": int(counts[3] or 0),
    }
    idle = (
        await s.execute(
            select(func.sum(Ad.remaining_budget)).where(
                Ad.account_id == acc.id,
                Ad.gone.is_(False),
                Ad.archived.is_(False),
                Ad.status == "active",
                Ad.is_paused.is_(False),
                Ad.remaining_budget > 0,
            )
        )
    ).scalar_one()
    out["balance"] = {
        "account": acc.remaining_budget,
        "on_ads": acc.ads_budget,
        "spent_total": acc.spent_budget,
        "on_running_ads": float(idle or 0),
    }
    out["target_cost_per_customer"] = st["target_cost_per_customer"]
    return out


async def daily(s: AsyncSession, acc: Account, days: int = 60) -> list[dict[str, Any]]:
    st = settings_of(acc)
    t = today_utc()
    since = t - timedelta(days=days - 1)
    rows = await s.execute(
        select(
            AdStat.day,
            func.sum(AdStat.views),
            func.sum(AdStat.clicks),
            func.sum(AdStat.actions),
            func.sum(AdStat.spent),
        )
        .where(AdStat.account_id == acc.id, AdStat.day >= since.isoformat())
        .group_by(AdStat.day)
    )
    have = {r[0]: r for r in rows}
    since_ts = int(datetime.combine(since, datetime.min.time(), UTC).timestamp())
    tz = zone(st.get("bot_timezone", "UTC"))
    sales: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    for ts, amount in await s.execute(
        select(BotTransaction.created_ts, BotTransaction.amount).where(
            BotTransaction.account_id == acc.id, _purchase_filter(st), BotTransaction.created_ts >= since_ts
        )
    ):
        d = datetime.fromtimestamp(int(ts), tz).date().isoformat()
        sales[d][0] += 1
        sales[d][1] += abs(float(amount or 0))
    attributed: dict[str, int] = defaultdict(int)
    for (ts,) in await s.execute(
        select(BotTransaction.created_ts)
        .join(BotUser, and_(BotUser.account_id == BotTransaction.account_id, BotUser.user_id == BotTransaction.user_id))
        .where(
            BotTransaction.account_id == acc.id,
            BotUser.tag_id.is_not(None),
            _purchase_filter(st),
            BotTransaction.created_ts >= since_ts,
        )
    ):
        attributed[datetime.fromtimestamp(int(ts), tz).date().isoformat()] += 1
    new_users: dict[str, int] = {}
    snaps = await s.execute(
        select(AccountSnapshot.ts, AccountSnapshot.bot_users)
        .where(AccountSnapshot.account_id == acc.id, AccountSnapshot.bot_users.is_not(None))
        .order_by(AccountSnapshot.ts)
    )
    per_day: dict[str, list[int]] = defaultdict(list)
    last: int | None = None
    for ts, users in snaps:
        d = datetime.fromtimestamp(int(ts), tz).date().isoformat()
        if not per_day[d] and last is not None:
            per_day[d].append(last)  # count growth across midnight into the new day
        per_day[d].append(int(users or 0))
        last = int(users or 0)
    for d, counts in per_day.items():
        new_users[d] = user_growth(counts)
    out = []
    for i in range(days):
        d = (since + timedelta(days=i)).isoformat()
        r = have.get(d)
        out.append(
            {
                "day": d,
                "views": int(r[1] or 0) if r else 0,
                "clicks": int(r[2] or 0) if r else 0,
                "starts": int(r[3] or 0) if r else 0,
                "spent": round(float(r[4] or 0), 5) if r else 0.0,
                "purchases": int(sales[d][0]) if d in sales else 0,
                "revenue": float(sales[d][1]) if d in sales else 0.0,
                "attributed_purchases": attributed.get(d, 0),
                "new_users": new_users.get(d),
            }
        )
    return out


async def live(s: AsyncSession, acc: Account) -> dict[str, Any]:
    """Intraday picture from snapshots: spend so far today and a points series for the live chart."""
    st = settings_of(acc)
    tz = zone(st.get("bot_timezone", "Asia/Tehran"))
    local_midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    start_ts = int(local_midnight.timestamp())
    snaps = list(
        await s.execute(
            select(
                AccountSnapshot.ts,
                AccountSnapshot.spent_budget,
                AccountSnapshot.views,
                AccountSnapshot.clicks,
                AccountSnapshot.actions,
                AccountSnapshot.bot_users,
            )
            .where(AccountSnapshot.account_id == acc.id, AccountSnapshot.ts >= start_ts - 3600)
            .order_by(AccountSnapshot.ts)
        )
    )
    base = next((r for r in reversed(snaps) if r[0] <= start_ts), snaps[0] if snaps else None)
    points = []
    if base is not None:
        step = max(1, len(snaps) // 300)
        for r in snaps[::step] + ([snaps[-1]] if snaps and (len(snaps) - 1) % step else []):
            if r[0] < start_ts:
                continue
            points.append(
                {
                    "ts": r[0],
                    "spent": round(r[1] - base[1], 5),
                    "starts": r[4] - base[4],
                    "clicks": r[3] - base[3],
                    "views": r[2] - base[2],
                }
            )
    last = snaps[-1] if snaps else None
    today = {
        "spent": round(last[1] - base[1], 5) if last and base else 0.0,
        "starts": (last[4] - base[4]) if last and base else 0,
        "clicks": (last[3] - base[3]) if last and base else 0,
        "views": (last[2] - base[2]) if last and base else 0,
        "new_users": user_growth([int(r[5]) for r in [base, *[x for x in snaps if x[0] > base[0]]] if r[5] is not None])
        if last and base and last[5] is not None
        else None,
    }
    return {"today": today, "points": points, "since_ts": start_ts}


async def tag_rows(s: AsyncSession, acc: Account) -> list[dict[str, Any]]:
    tags = list(
        (
            await s.execute(select(ReferralTag).where(ReferralTag.account_id == acc.id).order_by(ReferralTag.id))
        ).scalars()
    )
    sales = await tag_sales(s, acc)
    ads = list(
        (
            await s.execute(
                select(Ad.tag_id, Ad.ad_id, Ad.spent_budget, Ad.actions, Ad.title, Ad.status).where(
                    Ad.account_id == acc.id, Ad.gone.is_(False), Ad.archived.is_(False), Ad.tag_id.is_not(None)
                )
            )
        ).all()
    )
    by_tag: dict[int, list] = defaultdict(list)
    for row in ads:
        by_tag[int(row[0] or 0)].append(row)
    out = []
    for tg in tags:
        sale = sales.get(tg.id, {"users": 0, "buyers": 0, "purchases": 0, "revenue": 0.0})
        spend = sum(float(r[2] or 0) for r in by_tag[tg.id])
        starts = sum(int(r[3] or 0) for r in by_tag[tg.id])
        out.append(
            {
                "id": tg.id,
                "name": tg.name,
                "referrer_id": tg.referrer_id,
                "start_code": tg.start_code,
                "kind": tg.kind,
                "note": tg.note,
                "link": f"https://t.me/{acc.bot_username}?start={tg.start_code}" if acc.bot_username else "",
                "referred_count": tg.referred_count,
                "last_synced_ts": tg.last_synced_ts,
                "last_error": tg.last_error,
                "ads": [{"ad_id": r[1], "title": r[4], "status": r[5]} for r in by_tag[tg.id]],
                "ad_spend": spend,
                "ad_starts": starts,
                **sale,
                "cpp": _ratio(spend, sale["buyers"]),
                "buy_rate": _ratio(sale["buyers"], sale["users"]),
            }
        )
    return out


async def sales_overview(s: AsyncSession, acc: Account, days: int = 30) -> dict[str, Any]:
    st = settings_of(acc)
    since_ts = now_ts() - days * 86400
    by_type = [
        {"type": t or "—", "count": int(n), "amount": float(a or 0)}
        for t, n, a in await s.execute(
            select(BotTransaction.type, func.count(BotTransaction.id), func.sum(func.abs(BotTransaction.amount)))
            .where(BotTransaction.account_id == acc.id, BotTransaction.created_ts >= since_ts)
            .group_by(BotTransaction.type)
            .order_by(func.count(BotTransaction.id).desc())
        )
    ]
    total_tx = (
        await s.execute(select(func.count(BotTransaction.id)).where(BotTransaction.account_id == acc.id))
    ).scalar_one()
    untagged = (
        await s.execute(
            select(func.count(Ad.id)).where(
                Ad.account_id == acc.id,
                Ad.gone.is_(False),
                Ad.archived.is_(False),
                Ad.tag_id.is_(None),
                Ad.status.notin_(("declined", "stopped")),
            )
        )
    ).scalar_one()
    return {
        "untagged_ads": int(untagged),
        "days": days,
        "by_type": by_type,
        "purchase_types": st.get("purchase_types"),
        "transactions_stored": int(total_tx),
        "last_tx_ts": acc.last_tx_ts,
    }
