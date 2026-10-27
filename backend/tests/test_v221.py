"""v2.2.1: edits that never reached Telegram's review.

Since September 2026 the Ads API leaves an edited (or new) ad without budget as "ready_for_review": the change
is saved but nobody reviews it until submitAdForReview is called. The panel now submits such ads, checks that
Telegram really stored the change, and shows the status as its own."""

from sqlalchemy import select

from app.db.session import session_scope
from app.models import Ad
from app.services import actions
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


async def test_edit_shown_old_until_approval_is_checked_after_review(api, tg_mock):
    """Telegram may keep returning the approved text while the edit waits for review: not an error yet."""
    await _setup(api, tg_mock)
    tg_mock.edits_wait_for_review = True
    op = (
        await api.post(
            f"{A}/bulk", json={"kind": "content", "ad_ids": [3], "mode": "inplace", "text": TEXT, "confirm": True}
        )
    ).json()
    await _drain()
    (it,) = await _items(op["id"])
    assert it.status == "in_review" and "تا تأیید" in it.error
    tg_mock.review("a")
    await _sync_all()
    await _drain()
    (it,) = await _items(op["id"])
    assert it.status == "approved" and not it.error
    assert (await api.get(f"{A}/ads/3")).json()["ad"]["text"] == TEXT

    # A direct edit in the same situation is accepted with a note, not refused.
    r = await api.patch(f"{A}/ads/3", json={"text": TEXT + " ✅"})
    assert r.status_code == 200 and "تا تأیید" in r.json()["message"]


async def test_edit_approved_with_the_old_text_fails_with_what_telegram_has(api, tg_mock):
    await _setup(api, tg_mock)
    tg_mock.edits_wait_for_review = True
    tg_mock.drops_pending = True
    before = tg_mock.accounts["a"]["ads"][3]["text"]
    op = (
        await api.post(
            f"{A}/bulk", json={"kind": "content", "ad_ids": [3], "mode": "inplace", "text": TEXT, "confirm": True}
        )
    ).json()
    await _drain()
    tg_mock.review("a")
    await _sync_all()
    await _drain()
    (it,) = await _items(op["id"])
    assert it.status == "failed" and "تأیید کرد ولی متن جدید را اعمال نکرد" in it.error
    assert before.split()[0] in it.error  # the text Telegram kept is quoted


# ---------------------------------------------------------------- edit probe (which request format Telegram applies)


async def test_edit_probe_finds_the_format_telegram_applies_and_uses_it(api, tg_mock):
    await _setup(api, tg_mock)
    tg_mock.ignored_formats = {"json"}  # ok to JSON, content kept: the real account's symptom
    assert (await api.post(f"{A}/bulk/edit-probe", json={"ad_id": 3, "text": TEXT})).status_code == 400  # confirm
    r = await api.post(f"{A}/bulk/edit-probe", json={"ad_id": 3, "text": TEXT, "confirm": True})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["result"] == "form" and "فرم" in out["message"]
    assert [t["format"] for t in out["trace"]] == ["json", "form"]
    assert out["trace"][0]["applied"] is False and out["trace"][0]["answer"]["ok"] is True
    assert out["trace"][1]["read_back"]["text"] == TEXT
    caps = (await api.get(f"{A}/bulk/capabilities")).json()
    assert caps["edit_format"] == "form" and caps["edit_probe"]["result"] == "form"
    assert "mocktoken" not in r.text

    # Every later edit goes as a form and sticks.
    op = (
        await api.post(
            f"{A}/bulk", json={"kind": "content", "ad_ids": [4], "mode": "inplace", "text": TEXT + "!", "confirm": True}
        )
    ).json()
    await _drain()
    assert tg_mock.accounts["a"]["ads"][4]["text"] == TEXT + "!"
    assert (await _items(op["id"]))[0].status == "in_review"
    r = await api.patch(f"{A}/ads/5", json={"text": TEXT + "؟"})
    assert r.status_code == 200 and tg_mock.accounts["a"]["ads"][5]["text"] == TEXT + "؟"


async def test_edit_probe_when_no_format_works_steers_to_replacement(api, tg_mock):
    await _setup(api, tg_mock)
    tg_mock.ignores_edits = True
    out = (await api.post(f"{A}/bulk/edit-probe", json={"ad_id": 3, "text": TEXT, "confirm": True})).json()
    assert out["result"] == "none" and len(out["trace"]) == 3 and "جایگزینی" in out["message"]
    p = (
        await api.post(f"{A}/bulk/preview", json={"kind": "content", "ad_ids": [3], "mode": "inplace", "text": TEXT})
    ).json()
    assert any("جایگزینی" in e for e in p["errors"])
    p = (
        await api.post(f"{A}/bulk/preview", json={"kind": "content", "ad_ids": [3], "mode": "replace", "text": TEXT})
    ).json()
    assert p["errors"] == []
    r = await api.patch(f"{A}/ads/3", json={"text": TEXT})
    assert r.status_code == 400 and "جایگزینی" in r.json()["detail"]


async def test_edit_probe_stops_at_json_when_it_works(api, tg_mock):
    await _setup(api, tg_mock)
    out = (await api.post(f"{A}/bulk/edit-probe", json={"ad_id": 3, "text": TEXT, "confirm": True})).json()
    assert out["result"] == "json" and len(out["trace"]) == 1
    same = await api.post(f"{A}/bulk/edit-probe", json={"ad_id": 3, "text": TEXT, "confirm": True})
    assert same.status_code == 400  # the text is already the ad's text
    p = (
        await api.post(f"{A}/bulk/preview", json={"kind": "content", "ad_ids": [4], "mode": "inplace", "text": TEXT})
    ).json()
    assert not any("آزمایش نشده" in w for w in p["warnings"])


async def test_a_refused_withdrawal_is_retried_with_a_new_idempotency_key(api, tg_mock):
    """Telegram replays the stored answer of a key; AD_ACTIVE_RECENTLY must not be replayed forever."""
    from app.models import Job

    await _setup(api, tg_mock)
    tg_mock.inactive_seconds = 600
    r = await api.post(f"{A}/ads/3/actions", json={"action": "withdraw"})
    assert r.status_code == 200, r.text
    keys = []

    async def attempt() -> Job:
        await actions.process_due()
        async with session_scope() as s:
            job = (await s.execute(select(Job).where(Job.kind == "withdraw"))).scalar_one()
            keys.append(job.idem_key)
            job.run_at = 0
            return job

    job = await attempt()  # pauses the active ad and waits
    job = await attempt()  # refused: paused less than ten minutes ago
    assert job.status == "queued" and "۱۰ دقیقه" in job.result
    tg_mock.accounts["a"]["paused_at"][3] -= 601
    job = await attempt()
    assert job.status == "done" and tg_mock.accounts["a"]["ads"][3]["remaining_budget"] == 0
    assert keys[1] != keys[2] or keys[0] != keys[1]  # the refused try did not pin the key


def test_which_errors_get_a_new_key():
    from app.integrations.telegram_ads import TelegramAdsError
    from app.services.actions import answered_error

    assert answered_error(TelegramAdsError("decreaseAdBudget", "AD_ACTIVE_RECENTLY"))
    assert answered_error(TelegramAdsError("createAd", "IDEMPOTENT_PARAM_MISMATCH"))
    assert not answered_error(TelegramAdsError("createAd", "network error: ConnectTimeout"))
    assert not answered_error(TelegramAdsError("createAd", "HTTP 502", 502))
    assert not answered_error(TelegramAdsError("createAd", "IDEMPOTENT_REQUEST_IN_PROGRESS"))
