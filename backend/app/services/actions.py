"""Every change on Telegram goes through this queue: logged, retried safely (idempotency keys)
and checked against the account's spending caps."""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.core.logging import get_logger
from app.db.base import now_ts
from app.db.session import session_scope
from app.integrations.telegram_ads import TelegramAdsClient, TelegramAdsError, floor_amount
from app.models import Account, Ad, BulkItem, Job, Proposal
from app.services import budget, events
from app.services.accounts import settings_of
from app.services.sync import start_code_of

log = get_logger("actions")
MAX_ATTEMPTS = 4
JOB_LABEL = {
    "pause": "توقف",
    "resume": "فعال‌سازی",
    "withdraw": "برداشت بودجه",
    "topup": "شارژ",
    "transfer": "انتقال بودجه",
    "create_ad": "ساخت آگهی جایگزین",
    "edit_ad": "ویرایش آگهی",
    "delete_ad": "حذف آگهی",
}
# Errors that will not change on a retry: the item fails at once instead of four attempts.
FINAL_ERRORS = (
    "NOT_ENOUGH_BUDGET",
    "BUDGET_TOO_LOW",
    "AD_TEXT_TOO_LONG",
    "CHANNEL_INVALID",
    "BOT_INVALID",
    "AD_ID_INVALID",
)


def enqueue(
    s: AsyncSession,
    acc: Account,
    kind: str,
    ad_id: int,
    amount: float | None = None,
    *,
    delay: int = 0,
    proposal_id: int | None = None,
    actor: str = "user",
    params: dict[str, Any] | None = None,
    bulk_item_id: int | None = None,
) -> Job:
    now = now_ts()
    job = Job(
        account_id=acc.id,
        kind=kind,
        ad_id=ad_id,
        amount=amount,
        run_at=now + delay,
        proposal_id=proposal_id,
        created_ts=now,
        idem_key=str(uuid.uuid4()),
        params=params or {},
        bulk_item_id=bulk_item_id,
    )
    s.add(job)
    events.log(
        s,
        f"queue_{kind}",
        account_id=acc.id,
        actor=actor,
        ad_id=ad_id,
        detail=f"{amount}" if amount is not None else "",
    )
    return job


def stop_and_withdraw(
    s: AsyncSession,
    acc: Account,
    ad_id: int,
    *,
    proposal_id: int | None = None,
    actor: str = "user",
    bulk_item_id: int | None = None,
) -> None:
    enqueue(s, acc, "pause", ad_id, proposal_id=proposal_id, actor=actor, bulk_item_id=bulk_item_id)
    enqueue(
        s,
        acc,
        "withdraw",
        ad_id,
        delay=get_settings().withdraw_delay_seconds,
        proposal_id=proposal_id,
        actor=actor,
        bulk_item_id=bulk_item_id,
    )


async def topups_last_24h(s: AsyncSession, acc: Account) -> float:
    r = (
        await s.execute(
            select(func.sum(Job.amount)).where(
                Job.account_id == acc.id,
                # Only new money put on ads. Moving a withdrawn budget to a replacement ("transfer") adds
                # nothing; initial budgets of replacements have their own cap (new_budget_last_24h).
                Job.kind == "topup",
                Job.status.in_(("queued", "running", "done")),
                Job.created_ts >= now_ts() - 86400,
            )
        )
    ).scalar_one()
    return float(r or 0)


async def new_budget_last_24h(s: AsyncSession, acc: Account) -> float:
    """Initial budgets of ads created by bulk replacements in the last 24 h (bulk_daily_new_budget_cap)."""
    r = (
        await s.execute(
            select(func.sum(Job.amount)).where(
                Job.account_id == acc.id,
                Job.kind == "create_ad",
                Job.status.in_(("queued", "running", "done")),
                Job.created_ts >= now_ts() - 86400,
            )
        )
    ).scalar_one()
    return float(r or 0)


async def reserved_new_budget(s: AsyncSession, acc: Account) -> float:
    """Initial budgets of replacements queued but not created yet: already spoken for in the balance."""
    r = (
        await s.execute(
            select(func.sum(Job.amount)).where(
                Job.account_id == acc.id, Job.kind == "create_ad", Job.status.in_(("queued", "running"))
            )
        )
    ).scalar_one()
    return float(r or 0)


async def check_topup(s: AsyncSession, acc: Account, amount: float) -> None:
    st = settings_of(acc)
    if amount <= 0:
        raise AppError("مبلغ شارژ باید بیشتر از صفر باشد.")
    if amount > st["topup_max_per_ad"] + 1e-9:
        raise AppError(f"سقف شارژ هر آگهی {st['topup_max_per_ad']} است.")
    used = await topups_last_24h(s, acc)
    if used + amount > st["topup_daily_cap"] + 1e-9:
        raise AppError(f"سقف شارژ روزانه {st['topup_daily_cap']} است و {used:.2f} در ۲۴ ساعت اخیر استفاده شده.")


async def ad_action(s: AsyncSession, acc: Account, ad_id: int, action: str, amount: float | None) -> str:
    ad = (await s.execute(select(Ad).where(Ad.account_id == acc.id, Ad.ad_id == ad_id))).scalar_one_or_none()
    if ad is None:
        raise NotFound("این آگهی پیدا نشد.")
    if acc.automation_paused and action != "pause":
        raise AppError("اقدام‌های این حساب متوقف است؛ فقط «توقف» اجرا می‌شود.", code="automation_paused")
    if action == "pause":
        enqueue(s, acc, "pause", ad_id)
        return "توقف در صف رفت."
    if action == "resume":
        enqueue(s, acc, "resume", ad_id)
        return "فعال‌سازی در صف رفت."
    if action == "withdraw":
        stop_and_withdraw(s, acc, ad_id)
        return (
            f"آگهی متوقف می‌شود و حدود {max(1, get_settings().withdraw_delay_seconds // 60)} دقیقه بعد بودجه‌اش برمی‌گردد."
        )
    if action == "topup":
        amt = round(float(amount or 0), 2)
        await check_topup(s, acc, amt)
        enqueue(s, acc, "topup", ad_id, amt)
        return f"شارژ {amt} در صف رفت."
    raise AppError("اقدام ناشناخته.")


async def approve(s: AsyncSession, acc: Account, proposal_id: int, actor: str = "user") -> str | None:
    p = await s.get(Proposal, proposal_id)
    if p is None or p.account_id != acc.id:
        raise NotFound("این پیشنهاد پیدا نشد.")
    if p.status != "pending":
        raise AppError("این پیشنهاد دیگر در انتظار نیست.")
    if acc.automation_paused:
        raise AppError("اقدام‌های این حساب متوقف است.", code="automation_paused")
    if p.kind in ("stop", "withdraw"):
        stop_and_withdraw(s, acc, p.ad_id, proposal_id=p.id, actor=actor)
        p.status = "approved"
    elif p.kind in ("topup", "refill"):
        await check_topup(s, acc, p.amount)
        enqueue(s, acc, "topup", p.ad_id, p.amount, proposal_id=p.id, actor=actor)
        p.status = "approved"
    elif p.kind == "replace":
        raise AppError("جایگزینی با پنجره‌ی «ساخت آگهی جایگزین» (متن تازه و تأیید پیش‌نمایش) انجام می‌شود.")
    else:  # refresh: done by creating a new ad in the same placement
        p.status = "done"
    p.decided_ts = now_ts()
    events.log(s, f"approve_{p.kind}", account_id=acc.id, actor=actor, ad_id=p.ad_id, detail=f"پیشنهاد {p.id}")
    return p.kind


async def reject(s: AsyncSession, acc: Account, proposal_id: int, actor: str = "user") -> None:
    p = await s.get(Proposal, proposal_id)
    if p is None or p.account_id != acc.id:
        raise NotFound("این پیشنهاد پیدا نشد.")
    if p.status == "pending":
        p.status, p.decided_ts = "rejected", now_ts()
        events.log(s, "reject", account_id=acc.id, actor=actor, ad_id=p.ad_id, detail=f"پیشنهاد {p.id}")


async def kill_switch(s: AsyncSession, acc: Account) -> int:
    ads = list(
        (
            await s.execute(
                select(Ad).where(
                    Ad.account_id == acc.id, Ad.gone.is_(False), Ad.status == "active", Ad.is_paused.is_(False)
                )
            )
        ).scalars()
    )
    for ad in ads:
        enqueue(s, acc, "pause", ad.ad_id)
    for p in (
        await s.execute(select(Proposal).where(Proposal.account_id == acc.id, Proposal.status == "pending"))
    ).scalars():
        p.status, p.decided_ts, p.result = "rejected", now_ts(), "توقف کامل"
    acc.automation_paused = True
    events.log(s, "kill_switch", account_id=acc.id, actor="user", level="warn", detail=f"{len(ads)} آگهی برای توقف")
    return len(ads)


# ---------------------------------------------------------------- worker


async def _bulk_failed(s: AsyncSession, job: Job, msg: str) -> None:
    """A failed create/edit ends its bulk item; failures of later steps are judged by bulk.advance()."""
    if not job.bulk_item_id or job.kind not in ("create_ad", "edit_ad"):
        return
    it = await s.get(BulkItem, job.bulk_item_id)
    if it is not None and it.status == "submitted":
        it.status, it.error, it.updated_ts = "failed", msg[:500], now_ts()


async def _finish(job_id: int, status: str, result: str, extra: dict[str, Any] | None = None) -> None:
    async with session_scope() as s:
        job = await s.get(Job, job_id)
        if job is None:
            return
        job.status, job.result = status, result[:500]
        if extra:
            job.params = {**(job.params or {}), **extra}
        if status == "failed":
            await _bulk_failed(s, job, result)
        if job.proposal_id:
            await s.flush()
            left = (
                await s.execute(
                    select(func.count(Job.id)).where(
                        Job.proposal_id == job.proposal_id, Job.status.in_(("queued", "running"))
                    )
                )
            ).scalar_one()
            failed = (
                await s.execute(
                    select(func.count(Job.id)).where(Job.proposal_id == job.proposal_id, Job.status == "failed")
                )
            ).scalar_one()
            if left == 0:
                p = await s.get(Proposal, job.proposal_id)
                if p is not None:
                    p.status, p.result = ("failed" if failed else "done"), result[:500]
        events.log(
            s,
            f"{job.kind}_{status}",
            account_id=job.account_id,
            actor="worker",
            ad_id=job.ad_id,
            detail=result,
            level="error" if status == "failed" else "info",
        )


async def _requeue(job_id: int, msg: str, delay: int, *, count_attempt: bool = True, new_key: bool = False) -> None:
    async with session_scope() as s:
        job = await s.get(Job, job_id)
        if job is None:
            return
        if count_attempt and job.attempts + 1 >= MAX_ATTEMPTS:
            job.status, job.result = "failed", msg[:500]
            events.log(
                s,
                f"{job.kind}_failed",
                account_id=job.account_id,
                actor="worker",
                ad_id=job.ad_id,
                detail=msg,
                level="error",
            )
            if job.proposal_id:
                p = await s.get(Proposal, job.proposal_id)
                if p is not None:
                    p.status, p.result = "failed", msg[:500]
            await _bulk_failed(s, job, msg)
            return
        job.status = "queued"
        job.attempts += 1 if count_attempt else 0
        job.run_at = now_ts() + delay
        job.result = msg[:500]
        if new_key:
            job.idem_key = str(uuid.uuid4())


async def _refresh_ad(c: TelegramAdsClient, account_id: int, ad_id: int, kind: str | None = None) -> dict | None:
    ad = (await c.ads_by_id([ad_id])).get(ad_id)
    if ad:
        async with session_scope() as s:
            row = (
                await s.execute(select(Ad).where(Ad.account_id == account_id, Ad.ad_id == ad_id))
            ).scalar_one_or_none()
            if row is not None:
                row.status = str(ad.get("status") or row.status)
                row.is_paused = bool(ad.get("is_paused"))
                budget.observe(s, row, float(ad.get("remaining_budget") or 0), now_ts(), actor="app", kind=kind)
                row.spent_budget = float(ad.get("spent_budget") or 0)
    return ad


async def _store_balance(c: TelegramAdsClient, account_id: int) -> None:
    """Keep the stored account balance current after a job moved money (bulk waves plan with it)."""
    acct = await c.account()
    async with session_scope() as s:
        a = await s.get(Account, account_id)
        if a is not None:
            a.remaining_budget = float(acct.get("remaining_budget") or 0)
            a.ads_budget = float(acct.get("ads_budget") or a.ads_budget)


async def run_job(job: Job, c: TelegramAdsClient) -> None:
    kind, aid = job.kind, job.ad_id
    if kind in ("pause", "resume"):
        await c.set_paused(aid, kind == "pause")
        await _refresh_ad(c, job.account_id, aid)
        return await _finish(job.id, "done", "متوقف شد" if kind == "pause" else "دوباره فعال شد")
    if kind == "withdraw":
        ad = await _refresh_ad(c, job.account_id, aid)
        if not ad:
            return await _finish(job.id, "failed", "آگهی پیدا نشد")
        if not ad.get("is_paused") and ad.get("status") == "active":
            await c.set_paused(aid, True)
            return await _requeue(
                job.id, "آگهی هنوز فعال بود؛ متوقف شد و برداشت بعداً انجام می‌شود", get_settings().withdraw_delay_seconds
            )
        amount = floor_amount(float(ad.get("remaining_budget") or 0))
        if amount <= 0:
            return await _finish(job.id, "done", "بودجه‌ای برای برداشت نبود", {"done_amount": 0})
        await c.withdraw(aid, amount, key=job.idem_key)
        await _refresh_ad(c, job.account_id, aid, "withdraw")
        await _store_balance(c, job.account_id)
        return await _finish(job.id, "done", f"{amount} به حساب برگشت", {"done_amount": amount})
    if kind in ("topup", "transfer"):
        acct = await c.account()
        amount = float(job.amount or 0)
        if float(acct.get("remaining_budget") or 0) + 1e-9 < amount:
            async with session_scope() as s:
                waiting = (
                    await s.execute(
                        select(func.count(Job.id)).where(
                            Job.account_id == job.account_id,
                            Job.kind == "withdraw",
                            Job.status.in_(("queued", "running")),
                        )
                    )
                ).scalar_one()
            if waiting and job.created_ts > now_ts() - 3 * 3600:
                return await _requeue(
                    job.id,
                    "منتظر برگشت بودجه از آگهی‌های متوقف‌شده",
                    get_settings().withdraw_delay_seconds + 30,
                    count_attempt=False,
                )
            return await _finish(job.id, "failed", f"موجودی حساب ({acct.get('remaining_budget')}) کافی نیست")
        await c.topup(aid, amount, key=job.idem_key)
        await _refresh_ad(c, job.account_id, aid, "topup")
        await _store_balance(c, job.account_id)
        return await _finish(job.id, "done", f"{amount} شارژ شد")
    if kind == "create_ad":
        return await _run_create(job, c)
    if kind == "edit_ad":
        return await _run_edit(job, c)
    if kind == "delete_ad":
        await c.delete_ad(aid)
        async with session_scope() as s:
            row = (
                await s.execute(select(Ad).where(Ad.account_id == job.account_id, Ad.ad_id == aid))
            ).scalar_one_or_none()
            if row is not None:
                row.archived, row.archived_ts = True, now_ts()
        return await _finish(job.id, "done", "آگهی قدیمی حذف شد")
    return await _finish(job.id, "failed", f"نوع ناشناخته: {kind}")


async def _run_create(job: Job, c: TelegramAdsClient) -> None:
    from app.services import bulk

    params = dict(job.params or {})
    acct = await c.account()
    if float(acct.get("remaining_budget") or 0) + 1e-9 < float(params.get("initial_budget") or 0):
        # Not this ad's fault: it goes back to the queue and is sent with the next wave.
        async with session_scope() as s:
            it = await s.get(BulkItem, job.bulk_item_id) if job.bulk_item_id else None
            if it is not None and it.status == "submitted":
                it.status, it.updated_ts = "pending", now_ts()
            a = await s.get(Account, job.account_id)
            if a is not None:
                a.remaining_budget = float(acct.get("remaining_budget") or 0)
        return await _finish(
            job.id, "cancelled", f"موجودی حساب ({acct.get('remaining_budget')}) کافی نبود؛ آگهی به موج بعد رفت"
        )
    raw = await c.create_ad(job.idem_key, **params)
    await _store_balance(c, job.account_id)
    async with session_scope() as s:
        it = await s.get(BulkItem, job.bulk_item_id) if job.bulk_item_id else None
        if it is not None:
            ad = await bulk.store_created_ad(s, job.account_id, raw, params, it)
            it.new_ad_id = ad.ad_id
            it.status, it.updated_ts = "in_review", now_ts()
            events.log(
                s, "create_ad", account_id=job.account_id, actor="bulk", ad_id=ad.ad_id, detail=f"جایگزین {job.ad_id}"
            )
    return await _finish(job.id, "done", f"آگهی {raw.get('ad_id')} ساخته شد و به بررسی رفت")


async def _run_edit(job: Job, c: TelegramAdsClient) -> None:
    from app.services import bulk

    params = {k: v for k, v in (job.params or {}).items() if k in ("text", "promote_url", "photo_id")}
    photo = params.get("photo_id")
    try:
        raw = await c.edit_ad(job.ad_id, **params)
    except TelegramAdsError as e:
        if photo and "PHOTO" in e.error.upper():
            async with session_scope() as s:
                await bulk.set_capability(s, "edit_photo", "no", f"editAd: {e.error}")
        raise
    async with session_scope() as s:
        row = (
            await s.execute(select(Ad).where(Ad.account_id == job.account_id, Ad.ad_id == job.ad_id))
        ).scalar_one_or_none()
        it = await s.get(BulkItem, job.bulk_item_id) if job.bulk_item_id else None
        if photo:
            got = raw.get("photo")
            confirmed = isinstance(got, dict) and str(got.get("photo_id") or "") == str(photo)
            await bulk.set_capability(s, "edit_photo", "yes" if confirmed else "unknown", f"editAd on ad {job.ad_id}")
            if not confirmed and it is not None:
                it.error = "تلگرام تغییر عکس را در پاسخ تأیید نکرد؛ عکس آگهی را در ads.telegram.org بررسی کنید."
        if row is not None:
            row.text = str(raw.get("text") or params.get("text") or row.text)
            row.promote_url = str(raw.get("promote_url") or params.get("promote_url") or row.promote_url)
            row.start_code = start_code_of(row.promote_url)
            row.status = str(raw.get("status") or "in_review")
            row.decline_reason = str((raw.get("decline_reason") or {}).get("text") or "")
            if photo:
                row.has_photo = True
        if it is not None:
            if row is not None:
                if it.params.get("tag_id"):
                    row.tag_id = it.params["tag_id"]
                if it.params.get("variant_id") and "text" in params:
                    row.variant_id = it.params["variant_id"]
            it.status, it.updated_ts = "in_review", now_ts()
        events.log(
            s, "edit_ad", account_id=job.account_id, actor="bulk", ad_id=job.ad_id, detail=", ".join(sorted(params))
        )
    return await _finish(job.id, "done", "تغییر فرستاده شد؛ آگهی دوباره بررسی می‌شود")


async def process_due() -> int:
    now = now_ts()
    async with session_scope() as s:
        jobs = list(
            (await s.execute(select(Job).where(Job.status == "queued", Job.run_at <= now).order_by(Job.id))).scalars()
        )
        accounts = {a.id: a for a in (await s.execute(select(Account))).scalars()}
        claimed: list[Job] = []
        for job in jobs:
            acc = accounts.get(job.account_id)
            if acc is None:
                continue
            if acc.automation_paused and job.kind != "pause":
                continue
            job.status = "running"
            claimed.append(job)
        tokens = {a.id: crypto.decrypt(a.tg_token_enc) for a in accounts.values() if a.tg_token_enc}
        await s.flush()
        for j in claimed:
            s.expunge(j)
    bulk_accounts: set[int] = set()
    for job in claimed:
        if job.bulk_item_id:
            bulk_accounts.add(job.account_id)
        token = tokens.get(job.account_id)
        if not token:
            await _finish(job.id, "failed", "توکن Telegram Ads وارد نشده است")
            continue
        try:
            async with TelegramAdsClient(token) as c:
                await run_job(job, c)
        except TelegramAdsError as e:
            if job.bulk_item_id and job.kind in ("create_ad", "edit_ad", "delete_ad") and e.error in FINAL_ERRORS:
                await _finish(job.id, "failed", f"خطای تلگرام: {e.friendly}")
                continue
            delay = get_settings().withdraw_delay_seconds if job.kind == "withdraw" else 60
            await _requeue(job.id, f"خطای تلگرام: {e.friendly}", delay, new_key=e.error == "IDEMPOTENT_PARAM_MISMATCH")
        except Exception as e:  # keep the worker alive whatever happens to one job
            log.exception("job %s failed", job.id)
            await _requeue(job.id, f"خطا: {e}", 60)
    if bulk_accounts:
        from app.services import bulk

        for account_id in bulk_accounts:
            try:
                await bulk.advance(account_id)
            except Exception:
                log.exception("bulk advance for account %s failed", account_id)
    return len(claimed)
