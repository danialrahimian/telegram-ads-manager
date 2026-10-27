"""Bulk operations on ads, Telegram Ads API capabilities and the referrer pool of one account."""

from fastapi import APIRouter, File, UploadFile
from sqlalchemy import select

from app.api.deps import AccountDep, Auth, Session
from app.core.errors import AppError, UpstreamError
from app.integrations.telegram_ads import TelegramAdsError
from app.models import BulkOperation, ReferralTag
from app.schemas.requests import BulkIn, EditProbeIn, ResubmitIn
from app.services import accounts, bulk, events, referrals, tracking

router = APIRouter(prefix="/accounts/{slug}", tags=["bulk"], dependencies=[Auth])

MAX_PHOTO = 5 * 1024 * 1024


@router.get("/bulk")
async def list_ops(acc: AccountDep, s: Session) -> dict:
    ops = (
        await s.execute(
            select(BulkOperation).where(BulkOperation.account_id == acc.id).order_by(BulkOperation.id.desc()).limit(50)
        )
    ).scalars()
    return {"operations": [await bulk.op_public(s, op) for op in ops], "capabilities": await bulk.capabilities(s)}


@router.post("/bulk/preview")
async def preview(body: BulkIn, acc: AccountDep, s: Session) -> dict:
    p = await bulk.plan(s, acc, body.model_dump(exclude_none=True))
    for it in p["items"]:
        it.pop("params", None)
    return p


@router.post("/bulk", status_code=201)
async def create_op(body: BulkIn, acc: AccountDep, s: Session) -> dict:
    op = await bulk.create(s, acc, body.model_dump(exclude_none=True))
    op_id, account_id = op.id, acc.id
    await s.commit()
    await bulk.advance(account_id)  # first batch right away
    s.expire_all()
    fresh = await s.get(BulkOperation, op_id)
    assert fresh is not None
    return {**await bulk.op_public(s, fresh, with_items=True), "message": f"عملیات گروهی #{op_id} شروع شد."}


@router.get("/bulk/capabilities")
async def get_capabilities(acc: AccountDep, s: Session) -> dict:
    return await bulk.capabilities(s)


@router.post("/bulk/edit-probe")
async def edit_probe(body: EditProbeIn, acc: AccountDep, s: Session) -> dict:
    if not body.confirm:
        raise AppError("این آزمایش متن آگهی را واقعاً عوض می‌کند؛ تأیید لازم است.")
    out = await bulk.probe_edit(s, acc, body.ad_id, body.text, allow_pause=body.allow_pause)
    await s.commit()
    return out


@router.post("/bulk/capabilities/probe")
async def probe_capabilities(acc: AccountDep, s: Session) -> dict:
    cap = await bulk.probe(s, acc)
    await s.commit()
    return cap


@router.post("/bulk/photo")
async def upload_photo(acc: AccountDep, s: Session, photo: UploadFile = File(...)) -> dict:
    """Upload once (uploadAdPhoto); the returned photo_id is used for every ad of the operation."""
    content = await photo.read()
    if len(content) > MAX_PHOTO:
        raise AppError("عکس باید کمتر از ۵ مگابایت باشد.")
    if (photo.content_type or "") not in ("image/jpeg", "image/png"):
        raise AppError("عکس باید JPG یا PNG باشد.")
    try:
        async with accounts.tg_client(acc) as c:
            up = await c.upload_photo(photo.filename or "photo.jpg", content, photo.content_type or "image/jpeg")
    except TelegramAdsError as e:
        raise UpstreamError(f"تلگرام عکس را نپذیرفت: {e.friendly}") from None
    events.log(s, "photo_uploaded", account_id=acc.id, actor="user", detail=photo.filename or "")
    await s.commit()
    return {"photo_id": up.get("photo_id"), "photo_url": up.get("photo_url"), "name": photo.filename}


@router.get("/bulk/{op_id}")
async def get_op(op_id: int, acc: AccountDep, s: Session) -> dict:
    return await bulk.op_public(s, await bulk.get_op(s, acc, op_id), with_items=True)


@router.post("/bulk/{op_id}/stop")
async def stop_op(op_id: int, acc: AccountDep, s: Session) -> dict:
    n = await bulk.stop(s, acc, op_id)
    await s.commit()
    return {"cancelled": n, "message": f"عملیات متوقف شد؛ {n} کار اجرانشده لغو شد."}


@router.post("/bulk/{op_id}/items/{item_id}/resubmit")
async def resubmit(op_id: int, item_id: int, body: ResubmitIn, acc: AccountDep, s: Session) -> dict:
    await bulk.resubmit(s, acc, op_id, item_id, body.text)
    await s.commit()
    return {"message": "متن اصلاح‌شده فرستاده شد؛ آگهی دوباره بررسی می‌شود."}


# ---------------------------------------------------------------- referral tracking


@router.get("/tracking")
async def get_tracking(acc: AccountDep, s: Session) -> dict:
    tags = (
        await s.execute(select(ReferralTag).where(ReferralTag.account_id == acc.id).order_by(ReferralTag.id))
    ).scalars()
    return {
        "modes": [m.public() for m in tracking.MODES],
        "campaign_request": tracking.CAMPAIGN_REQUEST,
        "random_example": tracking.random_code(),
        "random": tracking.status(acc),
        "pool": await referrals.pool_status(s, acc),
        "tags": [{"id": t.id, "name": t.name, "start_code": t.start_code, "kind": t.kind} for t in tags],
    }


@router.post("/tracking/random/test")
async def start_random_test(acc: AccountDep, s: Session) -> dict:
    """Make a test code: start the bot with its link from an account that never used the bot, then check."""
    test = await tracking.start_test(s, acc)
    await s.commit()
    return {"test": test, "random": tracking.status(acc)}


@router.post("/tracking/random/check")
async def check_random_test(acc: AccountDep, s: Session) -> dict:
    test = await tracking.check_test(s, acc)
    await s.commit()
    return {"test": test, "random": tracking.status(acc)}


@router.post("/tracking/pool/check")
async def check_pool(acc: AccountDep, s: Session) -> dict:
    results = await referrals.check_pool(acc)
    state = dict(acc.sync_state or {})
    state["pool_checks"] = {**(state.get("pool_checks") or {}), **results}
    acc.sync_state = state
    await s.commit()
    return {"pool": await referrals.pool_status(s, acc)}
