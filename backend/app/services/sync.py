"""Synchronisation of one account with Telegram Ads and its sales bot.

Three independent jobs, each on its own configurable interval:
  fast   account balance + every ad's lifetime counters (cheap: 2-3 requests)
  stats  daily rows per ad for ads that changed (one request per ad)
  bot    sales bot stats, the transaction ledger (incremental) and users of each referral tag

Network calls happen outside database transactions so SQLite is never locked while waiting.
"""

import asyncio
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from app.core import crypto
from app.core.logging import get_logger
from app.db.base import now_ts
from app.db.session import session_scope
from app.integrations.bot_api import (
    BotApiClient,
    BotApiError,
    field_names,
    norm_transaction,
    norm_user,
    retryable,
    zone,
)
from app.integrations.telegram_ads import TelegramAdsClient, TelegramAdsError
from app.models import Account, AccountSnapshot, Ad, AdStat, BotTransaction, BotUser, ReferralTag
from app.services import bot_diag, budget, events, periods
from app.services.accounts import apply_account_info, settings_of

log = get_logger("sync")
DAY = 86400
SNAPSHOT_EVERY = 55  # seconds; snapshots are kept for the live chart
SNAPSHOT_KEEP_DAYS = 14
RUNNING_STATUSES = {"active", "on_hold", "in_review", "ready_for_review"}


class SyncSkipped(Exception):
    """The account is not configured for this kind of sync."""


def start_code_of(url: str) -> str:
    try:
        q = parse_qs(urlparse(url or "").query)
    except ValueError:
        return ""
    vals = q.get("start") or q.get("startapp") or []
    return vals[0][:128] if vals else ""


def target_summary(t: dict[str, Any] | None) -> tuple[str, str]:
    if not t:
        return "", ""
    parts: list[str] = []
    for key in ("channels", "bots"):
        for x in t.get(key) or []:
            if x.get("username"):
                parts.append("@" + str(x["username"]))
            else:
                parts.append(str(x.get("title") or x.get("channel_id") or x.get("bot_id") or ""))
    if t.get("search_queries"):
        parts.append("جست‌وجو: " + "، ".join(t["search_queries"]))
    for key, label in (("topics", "موضوع"), ("languages", "زبان"), ("countries", "کشور")):
        if t.get(key):
            parts.append(label + ": " + "، ".join(str(x.get("name", "")) for x in t[key]))
    return str(t.get("type", "")), " | ".join(p for p in parts if p)


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")


async def _mark(account_id: int, kind: str, *, ok: bool, error: str = "", started: float, extra: dict | None = None):
    async with session_scope() as s:
        acc = await s.get(Account, account_id)
        if acc is None:
            return
        state = dict(acc.sync_state or {})
        st = dict(state.get(kind) or {})
        st["last_try"] = now_ts()
        st["duration"] = round(time.monotonic() - started, 2)
        if ok:
            st.update(last_ok=st["last_try"], error="", fails=0)
        else:
            st.update(error=error[:500], fails=int(st.get("fails") or 0) + 1)
        if extra:
            st.update(extra)
        state[kind] = st
        acc.sync_state = state


async def _load(account_id: int) -> tuple[Account, dict[str, Any]]:
    async with session_scope() as s:
        acc = await s.get(Account, account_id)
        if acc is None:
            raise SyncSkipped("account deleted")
        s.expunge(acc)
        return acc, settings_of(acc)


# ---------------------------------------------------------------- fast


async def fast_sync(account_id: int) -> dict[str, Any]:
    try:
        return await _fast_sync(account_id)
    except IntegrityError:
        # An ad created from the panel was inserted at the same moment; the second pass updates it.
        return await _fast_sync(account_id)


async def _fast_sync(account_id: int) -> dict[str, Any]:
    started = time.monotonic()
    acc, _ = await _load(account_id)
    token = crypto.decrypt(acc.tg_token_enc)
    if not token:
        raise SyncSkipped("no token")
    try:
        async with TelegramAdsClient(token) as c:
            info = await c.account()
            ads = await c.ads()
    except TelegramAdsError as e:
        async with session_scope() as s:
            a = await s.get(Account, account_id)
            if a is not None:
                a.tg_status, a.tg_error, a.tg_checked_ts = "error", e.friendly, now_ts()
        await _mark(account_id, "fast", ok=False, error=e.friendly, started=started)
        raise
    now = now_ts()
    async with session_scope() as s:
        a = await s.get(Account, account_id)
        if a is None:
            raise SyncSkipped("account deleted")
        apply_account_info(a, info)
        a.tg_status, a.tg_error, a.tg_checked_ts = "ok", "", now
        tags = {
            t.start_code: t.id
            for t in (await s.execute(select(ReferralTag).where(ReferralTag.account_id == account_id))).scalars()
        }
        existing = {ad.ad_id: ad for ad in (await s.execute(select(Ad).where(Ad.account_id == account_id))).scalars()}
        seen: set[int] = set()
        totals = {"views": 0, "clicks": 0, "actions": 0}
        for raw in ads:
            ad_id = int(raw["ad_id"])
            seen.add(ad_id)
            row = existing.get(ad_id)
            fresh = row is None
            if row is None:
                row = Ad(account_id=account_id, ad_id=ad_id, remaining_budget=0.0, funded_since_ts=0, empty_since_ts=0)
                s.add(row)
            typ, label = target_summary(raw.get("target"))
            row.title = str(raw.get("title") or "")[:200]
            row.text = str(raw.get("text") or "")
            row.promote_url = str(raw.get("promote_url") or "")[:300]
            row.start_code = start_code_of(row.promote_url)
            row.tag_id = tags.get(row.start_code) if row.start_code else None
            row.cpm = float(raw.get("cpm") or 0)
            row.placement = str(raw.get("placement") or "")
            row.status = str(raw.get("status") or "")
            row.is_paused = bool(raw.get("is_paused"))
            row.views = int(raw.get("views") or 0)
            row.clicks = int(raw.get("clicks") or 0)
            row.actions = int(raw.get("actions", raw.get("joins", 0)) or 0)
            row.action_type = str(raw.get("action_type") or "")
            row.spent_budget = float(raw.get("spent_budget") or 0)
            row.daily_budget_limit = float(raw.get("daily_budget_limit") or 0)
            row.created_date = int(raw.get("created_date") or 0)
            remaining = float(raw.get("remaining_budget") or 0)
            if fresh:
                budget.first_seen(row, remaining, now)
            else:
                budget.observe(s, row, remaining, now)
            row.target_type, row.target_label = typ, label
            row.target = raw.get("target") or {}
            row.has_photo, row.has_video = bool(raw.get("photo")), bool(raw.get("video"))
            row.decline_reason = str((raw.get("decline_reason") or {}).get("text") or "")
            row.gone = False
            row.synced_ts = now
            for k in totals:
                totals[k] += getattr(row, k)
        for ad_id, row in existing.items():
            if ad_id not in seen and not row.gone:
                row.gone = True
        await s.flush()
        live = [
            r for r in (await s.execute(select(Ad).where(Ad.account_id == account_id, Ad.gone.is_(False)))).scalars()
        ]
        await periods.record_baselines(s, a, live, now)
        last = (
            await s.execute(
                select(AccountSnapshot.ts)
                .where(AccountSnapshot.account_id == account_id)
                .order_by(AccountSnapshot.ts.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if last is None or now - last >= SNAPSHOT_EVERY:
            s.add(
                AccountSnapshot(
                    account_id=account_id,
                    ts=now,
                    spent_budget=a.spent_budget,
                    remaining_budget=a.remaining_budget,
                    ads_budget=a.ads_budget,
                    bot_users=(a.bot_stats or {}).get("count_user"),
                    **totals,
                )
            )
        if now % 3600 < 60:
            await s.execute(
                delete(AccountSnapshot).where(
                    AccountSnapshot.account_id == account_id, AccountSnapshot.ts < now - SNAPSHOT_KEEP_DAYS * DAY
                )
            )
    await _mark(account_id, "fast", ok=True, started=started, extra={"ads": len(ads)})
    return {"ads": len(ads)}


# ---------------------------------------------------------------- stats


async def stats_sync(account_id: int) -> dict[str, Any]:
    started = time.monotonic()
    acc, _ = await _load(account_id)
    token = crypto.decrypt(acc.tg_token_enc)
    if not token:
        raise SyncSkipped("no token")
    async with session_scope() as s:
        ads = list((await s.execute(select(Ad).where(Ad.account_id == account_id, Ad.gone.is_(False)))).scalars())
        last_days: dict[int, str] = {
            int(r[0]): str(r[1])
            for r in (
                await s.execute(
                    select(AdStat.ad_id, func.max(AdStat.day))
                    .where(AdStat.account_id == account_id)
                    .group_by(AdStat.ad_id)
                )
            ).all()
        }
        todo = [
            (a.ad_id, a.created_date, a.spent_budget)
            for a in ads
            if a.status in RUNNING_STATUSES or abs(a.stats_spent - a.spent_budget) > 1e-9 or a.ad_id not in last_days
        ]
    now = now_ts()
    to_time = (now // DAY + 1) * DAY
    fetched, errors = 0, []
    results: dict[int, tuple[float, list[dict[str, Any]]]] = {}
    async with TelegramAdsClient(token) as c:
        for ad_id, created, spent in todo:
            last = last_days.get(ad_id)
            if last:
                start = int(datetime.strptime(last, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()) - 2 * DAY
            else:
                start = created or (now - 90 * DAY)
            start = max(start - start % DAY, to_time - 999 * DAY)
            try:
                results[ad_id] = (spent, await c.ad_stats_daily(ad_id, start, to_time))
                fetched += 1
            except TelegramAdsError as e:
                errors.append(f"{ad_id}: {e.error}")
                if e.error == "ACCESS_TOKEN_INVALID":
                    break
            await asyncio.sleep(0.05)
    async with session_scope() as s:
        for ad_id, (spent, items) in results.items():
            for it in items:
                stmt = sqlite_insert(AdStat).values(
                    account_id=account_id,
                    ad_id=ad_id,
                    day=_day(int(it["from_time"])),
                    views=int(it.get("views") or 0),
                    clicks=int(it.get("clicks") or 0),
                    actions=int(it.get("actions", it.get("joins", 0)) or 0),
                    spent=float(it.get("spent_budget") or 0),
                )
                await s.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["account_id", "ad_id", "day"],
                        set_={
                            "views": stmt.excluded.views,
                            "clicks": stmt.excluded.clicks,
                            "actions": stmt.excluded.actions,
                            "spent": stmt.excluded.spent,
                        },
                    )
                )
            ad = (await s.execute(select(Ad).where(Ad.account_id == account_id, Ad.ad_id == ad_id))).scalar_one()
            ad.stats_ts, ad.stats_spent = now, spent
    if errors and not fetched:
        await _mark(account_id, "stats", ok=False, error=errors[0], started=started)
        raise TelegramAdsError("getAdStats", errors[0])
    await _mark(account_id, "stats", ok=True, started=started, extra={"ads": fetched, "errors": errors[:10]})
    return {"ads": fetched, "errors": errors}


# ---------------------------------------------------------------- bot


async def bot_sync(account_id: int) -> dict[str, Any]:
    started = time.monotonic()
    acc, st = await _load(account_id)
    key = crypto.decrypt(acc.bot_api_key_enc)
    if not acc.bot_base_url or not key:
        raise SyncSkipped("bot not configured")
    tz = zone(st.get("bot_timezone", "UTC"))
    purchase_types = [str(t) for t in st.get("purchase_types") or [] if str(t)]
    async with session_scope() as s:
        tags = [
            (t.id, t.referrer_id)
            for t in (await s.execute(select(ReferralTag).where(ReferralTag.account_id == account_id))).scalars()
        ]
    now = now_ts()
    from_ts = max(acc.last_tx_ts - 2 * DAY, 0) if acc.last_tx_ts else now - 120 * DAY
    stats: dict[str, Any] | None = None
    txs: list[dict[str, Any]] = []
    tag_users: dict[int, list[dict[str, Any]]] = {}
    tag_errors: dict[int, str] = {}
    errors: list[str] = []
    fields = dict(acc.bot_fields or {})
    parts: dict[str, Any] = {}

    def part(entry: dict[str, Any] | None, *, expect_rows: bool, **extra: Any) -> dict[str, Any]:
        cause = bot_diag.classify(entry or {}, expect_rows=expect_rows)
        return {"ok": cause["code"] in ("ok", "ok_empty"), **cause, "msg": (entry or {}).get("msg", ""), **extra}

    async with BotApiClient(acc.bot_base_url, key) as c:
        try:
            stats = await c.statbot()
            parts["stats"] = part(c.trace[-1], expect_rows=False, count_user=stats.get("count_user"))
        except BotApiError as e:
            errors.append(e.friendly)
            parts["stats"] = part(c.trace[-1] if c.trace else None, expect_rows=False, error=e.friendly)

        async def ledger(tx_type: str | None = None) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            limit = 500
            for page in range(1, 101):
                rows = await c.transactions(page=page, limit=limit, from_ts=from_ts, tx_type=tx_type)
                if page == 1 and rows:
                    fields["transactions"] = field_names(rows)
                batch = [norm_transaction(r, tz) for r in rows]
                out.extend(batch)
                if len(rows) < limit:
                    break
                # Stop paging once a whole page is older than the window (bot ignoring `from`).
                if all(0 < t["created_ts"] < from_ts for t in batch):
                    break
            return out

        filters = {"from": from_ts, "purchase_types": purchase_types}
        try:
            txs = await ledger()
            fields["transactions_mode"] = ["all"]
            parts["transactions"] = part(c.trace[-1], expect_rows=True, rows=len(txs), mode="all", filters=filters)
        except BotApiError as e:
            # Some bot builds crash on the unfiltered ledger but answer the documented per-type query.
            per_type: list[dict[str, Any]] = []
            try:
                if not (retryable(e) and purchase_types):
                    raise e
                for ptype in purchase_types:
                    per_type.extend(await ledger(ptype))
                txs = per_type
                fields["transactions_mode"] = ["per_type"]
                parts["transactions"] = part(
                    c.trace[-1], expect_rows=True, rows=len(txs), mode="per_type", filters=filters
                )
            except BotApiError:
                errors.append(e.friendly)
                parts["transactions"] = part(c.trace[-1], expect_rows=True, error=e.friendly, filters=filters)
        tag_parts: dict[str, Any] = {}
        for tag_id, referrer in tags:
            users: list[dict[str, Any]] = []
            try:
                for page in range(1, 51):
                    rows = await c.affiliates(referrer, page=page, limit=1000)
                    if page == 1 and rows:
                        fields["affiliates_users"] = field_names(rows)
                    users.extend(norm_user(r, tz) for r in rows)
                    if len(rows) < 1000:
                        break
                    if len({u["user_id"] for u in users}) < len(users):  # paging ignored: same rows again
                        break
                tag_users[tag_id] = users
                tag_parts[str(tag_id)] = part(c.trace[-1], expect_rows=True, rows=len(users), referrer=referrer)
            except BotApiError as e:
                tag_errors[tag_id] = e.friendly
                tag_parts[str(tag_id)] = part(c.trace[-1], expect_rows=True, error=e.friendly, referrer=referrer)
        parts["tags"] = tag_parts
    async with session_scope() as s:
        a = await s.get(Account, account_id)
        if a is None:
            raise SyncSkipped("account deleted")
        a.bot_fields = fields
        a.bot_checked_ts = now
        if stats is not None:
            prev = (a.sync_state or {}).get("bot_users_last")
            cur = stats.get("count_user")
            if bot_diag.users_jump(prev if isinstance(prev, int) else None, cur if isinstance(cur, int) else None):
                warn = f"تعداد کاربران ربات ناگهان از {prev} به {cur} رسید؛ احتمالاً ربات یا کلید دیگری وصل شده است."
                parts["stats"]["warning"] = warn
                events.log(s, "bot_users_jump", account_id=account_id, detail=warn, level="warn")
            state = dict(a.sync_state or {})
            if isinstance(cur, int):
                state["bot_users_last"] = cur
            a.sync_state = state
            a.bot_stats = stats
        if errors and stats is None and not txs:
            a.bot_status, a.bot_error = "error", errors[0]
        elif errors:
            a.bot_status, a.bot_error = "warn", errors[0]
        else:
            a.bot_status, a.bot_error = "ok", ""
        newest = a.last_tx_ts
        for t in txs:
            if not t["ext_id"]:
                continue
            stmt = sqlite_insert(BotTransaction).values(account_id=account_id, **t)
            await s.execute(
                stmt.on_conflict_do_update(
                    index_elements=["account_id", "ext_id"],
                    set_={
                        "amount": stmt.excluded.amount,
                        "type": stmt.excluded.type,
                        "user_id": stmt.excluded.user_id,
                        "created_ts": stmt.excluded.created_ts,
                        "source": stmt.excluded.source,
                    },
                )
            )
            if t["created_ts"] > newest:
                newest = t["created_ts"]
        a.last_tx_ts = min(newest, now) if newest else a.last_tx_ts
        for tag_id, users in tag_users.items():
            tag = await s.get(ReferralTag, tag_id)
            if tag is None:
                continue
            for u in users:
                if not u["user_id"]:
                    continue
                stmt = sqlite_insert(BotUser).values(
                    account_id=account_id,
                    user_id=u["user_id"],
                    tag_id=tag_id,
                    registered_ts=u["registered_ts"],
                    first_seen_ts=now,
                )
                await s.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["account_id", "user_id"],
                        set_={"tag_id": stmt.excluded.tag_id, "registered_ts": stmt.excluded.registered_ts},
                    )
                )
            tag.referred_count = len({u["user_id"] for u in users if u["user_id"]})
            tag.last_synced_ts, tag.last_error = now, ""
        for tag_id, err in tag_errors.items():
            tag = await s.get(ReferralTag, tag_id)
            if tag is not None:
                tag.last_error = err
        if errors:
            events.log(s, "bot_sync_warning", account_id=account_id, detail=errors[0], level="warn")
    ok = not (errors and stats is None and not txs)
    await _mark(
        account_id,
        "bot",
        ok=ok,
        error=errors[0] if errors else "",
        started=started,
        extra={"transactions": len(txs), "tags": len(tag_users), "warnings": errors[:5], "parts": parts},
    )
    if not ok:
        raise BotApiError(errors[0])
    return {"transactions": len(txs), "tags": len(tag_users), "errors": errors}


async def run(account_id: int, kind: str) -> dict[str, Any]:
    fn = {"fast": fast_sync, "stats": stats_sync, "bot": bot_sync}[kind]
    result = await fn(account_id)
    if kind == "fast":
        from app.services import bulk

        await bulk.advance(account_id)  # review results of bulk operations arrive with the ad list
    if kind in ("fast", "stats"):
        from app.services import rules

        await rules.generate(account_id)
    return result
