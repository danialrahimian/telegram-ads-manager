from datetime import date

from fastapi import APIRouter, File, Form, Query, UploadFile
from sqlalchemy import func, or_, select

from app.api.deps import AccountDep, Auth, Session
from app.core.errors import AppError, NotFound
from app.db.base import now_ts
from app.models import Ad, AdStat, BulkItem, Job, Proposal
from app.schemas.requests import AdActionIn, AdCreateIn, AdEditIn
from app.services import actions, ads, budget, metrics, periods
from app.services.rules import KIND_LABEL

router = APIRouter(prefix="/accounts/{slug}/ads", tags=["ads"], dependencies=[Auth])

MAX_PHOTO = 5 * 1024 * 1024


@router.get("")
async def list_ads(
    acc: AccountDep,
    s: Session,
    since: str | None = None,
    archived: bool = False,
    period: str | None = None,
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
) -> dict:
    """Ads with lifetime numbers and, under `period_stats`, the numbers of the chosen period
    (today, yesterday, 7d, 30d, month, all, custom with from/to) — see services/periods.py."""
    if since:
        try:
            since = date.fromisoformat(since).isoformat()
        except ValueError:
            since = None
    archived_count = (
        await s.execute(
            select(func.count(Ad.id)).where(Ad.account_id == acc.id, Ad.archived.is_(True), Ad.gone.is_(False))
        )
    ).scalar_one()
    rows = await metrics.ad_rows(s, acc, since, archived=archived)
    stats, meta = await periods.ad_period_stats(s, acc, period, date_from, date_to, now=now_ts())
    empty = {"views": 0, "clicks": 0, "actions": 0, "spent": 0.0}
    for r in rows:
        r["period_stats"] = stats.get(r["ad_id"], empty)
    return {"ads": rows, "since": since, "archived_count": archived_count, "period": meta}


@router.get("/{ad_id}")
async def get_ad(ad_id: int, acc: AccountDep, s: Session) -> dict:
    row = next((r for r in await metrics.ad_rows(s, acc, archived=None) if r["ad_id"] == ad_id), None)
    if row is None:
        raise NotFound("این آگهی پیدا نشد.")
    target = (await s.execute(select(Ad.target).where(Ad.account_id == acc.id, Ad.ad_id == ad_id))).scalar_one()
    daily = [
        {"day": d.day, "views": d.views, "clicks": d.clicks, "starts": d.actions, "spent": d.spent}
        for d in (
            await s.execute(
                select(AdStat).where(AdStat.account_id == acc.id, AdStat.ad_id == ad_id).order_by(AdStat.day)
            )
        ).scalars()
    ]
    jobs = [
        {
            "id": j.id,
            "kind": j.kind,
            "amount": j.amount,
            "status": j.status,
            "result": j.result,
            "created_ts": j.created_ts,
            "run_at": j.run_at,
        }
        for j in (
            await s.execute(
                select(Job).where(Job.account_id == acc.id, Job.ad_id == ad_id).order_by(Job.id.desc()).limit(20)
            )
        ).scalars()
    ]
    props = [
        {
            "id": p.id,
            "kind": p.kind,
            "label": KIND_LABEL.get(p.kind, p.kind),
            "amount": p.amount,
            "metrics": p.metrics,
            "status": p.status,
            "reason": p.reason,
            "result": p.result,
            "created_ts": p.created_ts,
        }
        for p in (
            await s.execute(
                select(Proposal)
                .where(Proposal.account_id == acc.id, Proposal.ad_id == ad_id)
                .order_by(Proposal.id.desc())
                .limit(20)
            )
        ).scalars()
    ]
    bulk_items = [
        {"op_id": it.op_id, "item_id": it.id, "status": it.status, "new_ad_id": it.new_ad_id, "old_ad_id": it.ad_id}
        for it in (
            await s.execute(
                select(BulkItem)
                .where(BulkItem.account_id == acc.id, or_(BulkItem.ad_id == ad_id, BulkItem.new_ad_id == ad_id))
                .order_by(BulkItem.id.desc())
                .limit(10)
            )
        ).scalars()
    ]
    return {
        "ad": row,
        "target": target,
        "daily": daily,
        "jobs": jobs,
        "proposals": props,
        "budget_history": await budget.history(s, acc.id, ad_id),
        "bulk": bulk_items,
    }


@router.post("/{ad_id}/actions")
async def ad_action(ad_id: int, body: AdActionIn, acc: AccountDep, s: Session) -> dict:
    msg = await actions.ad_action(s, acc, ad_id, body.action, body.amount)
    await s.commit()
    return {"message": msg}


@router.patch("/{ad_id}")
async def edit_ad(ad_id: int, body: AdEditIn, acc: AccountDep, s: Session) -> dict:
    data = body.model_dump(exclude_unset=True)
    if data.pop("clear_tag", False):
        data["tag_id"] = None
    _, remote = await ads.edit(s, acc, ad_id, data)
    await s.commit()
    return {
        "remote_changed": remote,
        "message": "تغییر به تلگرام فرستاده شد؛ آگهی دوباره بررسی می‌شود." if remote else "ذخیره شد.",
    }


@router.post("", status_code=201)
async def create_ad(
    acc: AccountDep, s: Session, payload: str = Form(...), photo: UploadFile | None = File(None)
) -> dict:
    """multipart: `payload` is the JSON body (AdCreateIn), `photo` an optional image."""
    body = AdCreateIn.model_validate_json(payload)
    ph = None
    if photo is not None and photo.filename:
        content = await photo.read()
        if len(content) > MAX_PHOTO:
            raise AppError("عکس باید کمتر از ۵ مگابایت باشد.")
        if (photo.content_type or "") not in ("image/jpeg", "image/png"):
            raise AppError("عکس باید JPG یا PNG باشد.")
        ph = (photo.filename, content, photo.content_type or "image/jpeg")
    ad = await ads.create(s, acc, body.model_dump(), ph)
    await s.commit()
    return {"ad_id": ad.ad_id, "message": f"آگهی {ad.ad_id} ساخته شد و برای بررسی تلگرام فرستاده شد."}
