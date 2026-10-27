"""v2.2.1: edits that never reached Telegram's review.

Since September 2026 the Ads API leaves an edited (or new) ad without budget as "ready_for_review": the change
is saved but nobody reviews it until submitAdForReview is called. The panel now submits such ads, checks that
Telegram really stored the change, and shows the status as its own."""

from sqlalchemy import select

from app.db.session import session_scope
from app.models import Ad
from tests.test_v21 import A, _drain, _id, _items, _setup, _sync_all

TEXT = "سایکونت | اینترنت پایدار با تست رایگان 👇"


def _submits(tg_mock) -> int:
    return sum(1 for _, m in tg_mock.calls if m == "submitAdForReview")


async def test_in_place_edit_of_an_ad_without_budget_is_submitted_for_review(api, tg_mock):
    await _setup(api, tg_mock)
    # 11 (refill_gap) has no budget, 3 (good_plenty) has 3 TON.
    op = (
        await api.post(
            f"{A}/bulk", json={"kind": "content", "ad_ids": [11, 3], "mode": "inplace", "text": TEXT, "confirm": True}
        )
    ).json()
    await _drain()
    ads = tg_mock.accounts["a"]["ads"]
    assert ads[11]["text"] == TEXT and ads[3]["text"] == TEXT
    # Telegram put both into review: 3 by itself, 11 only after submitAdForReview.
    assert ads[11]["status"] == "in_review" and ads[3]["status"] == "in_review"
    assert _submits(tg_mock) == 1
    items = await _items(op["id"])
    assert {i.status for i in items} == {"in_review"} and not any(i.error for i in items)
    tg_mock.review("a")
    await _sync_all()
    await _drain()
    assert {i.status for i in await _items(op["id"])} == {"approved"}


async def test_direct_edit_without_budget_is_submitted(api, tg_mock):
    await _setup(api, tg_mock)
    r = await api.patch(f"{A}/ads/11", json={"text": TEXT})
    assert r.status_code == 200, r.text
    assert tg_mock.accounts["a"]["ads"][11]["status"] == "in_review"
    assert _submits(tg_mock) == 1
    ad = (await api.get(f"{A}/ads/11")).json()["ad"]
    assert ad["text"] == TEXT and ad["status"] == "in_review"


async def test_an_edit_telegram_did_not_store_is_reported_not_hidden(api, tg_mock):
    await _setup(api, tg_mock)
    tg_mock.ignores_edits = True
    before = tg_mock.accounts["a"]["ads"][3]["text"]
    op = (
        await api.post(
            f"{A}/bulk", json={"kind": "content", "ad_ids": [3], "mode": "inplace", "text": TEXT, "confirm": True}
        )
    ).json()
    await _drain()
    (it,) = await _items(op["id"])
    assert it.status == "failed" and "متن آگهی عوض نشد" in it.error
    # The panel keeps what Telegram has, not what was asked for.
    ad = (await api.get(f"{A}/ads/3")).json()["ad"]
    assert ad["text"] == before

    r = await api.patch(f"{A}/ads/3", json={"text": TEXT})
    assert r.status_code == 502 and "عوض نشد" in r.json()["detail"]
    assert (await api.get(f"{A}/ads/3")).json()["ad"]["text"] == before


async def test_a_stuck_ad_can_be_sent_for_review(api, tg_mock):
    await _setup(api, tg_mock)
    tg_mock.set_ad("a", 11, status="ready_for_review", text=TEXT)
    await _sync_all()
    assert (await api.get(f"{A}/ads/11")).json()["ad"]["status"] == "ready_for_review"
    # Only an ad waiting for submission can be submitted.
    r = await api.post(f"{A}/ads/3/actions", json={"action": "submit_review"})
    assert r.status_code == 400
    r = await api.post(f"{A}/ads/11/actions", json={"action": "submit_review"})
    assert r.status_code == 200, r.text
    await _drain()
    assert tg_mock.accounts["a"]["ads"][11]["status"] == "in_review"
    async with session_scope() as s:
        row = (await s.execute(select(Ad).where(Ad.account_id == await _id(), Ad.ad_id == 11))).scalar_one()
        assert row.status == "in_review"


async def test_bulk_engine_submits_an_item_left_ready_for_review(api, tg_mock):
    await _setup(api, tg_mock)
    op = (
        await api.post(
            f"{A}/bulk", json={"kind": "content", "ad_ids": [3], "mode": "inplace", "text": TEXT, "confirm": True}
        )
    ).json()
    await _drain()
    # Telegram later puts it back to "ready_for_review" (e.g. the budget was withdrawn on ads.telegram.org).
    tg_mock.set_ad("a", 3, status="ready_for_review")
    await _sync_all()
    await _drain()
    assert tg_mock.accounts["a"]["ads"][3]["status"] == "in_review"
    assert (await _items(op["id"]))[0].status == "in_review"


async def test_submit_every_ad_waiting_for_review(api, tg_mock):
    await _setup(api, tg_mock)
    for ad_id in (10, 11):
        tg_mock.set_ad("a", ad_id, status="ready_for_review")
    await _sync_all()
    r = await api.post(f"{A}/ads/submit-review")
    assert r.status_code == 200 and r.json()["count"] == 2
    # A second click while the first jobs wait does not queue them twice.
    assert (await api.post(f"{A}/ads/submit-review")).json()["count"] == 2
    await _drain()
    assert _submits(tg_mock) == 2
    assert {tg_mock.accounts["a"]["ads"][i]["status"] for i in (10, 11)} == {"in_review"}
    assert (await api.post(f"{A}/ads/submit-review")).json()["count"] == 0
