"""v2.2: photos only on channel ads, Telegram's minimum initial budget and replacement waves, shared and
random referral links (with the random-code tracking test), per-period ad numbers and the dashboard's today."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.session import session_scope
from app.models import Account, AccountSnapshot, BulkItem, Job, ReferralTag
from app.services import actions, periods, sync, tracking
from tests.conftest import make_account
from tests.test_v21 import A, _drain, _id, _items, _setup, _sync_all


async def _photo(api) -> str:
    r = await api.post(f"{A}/bulk/photo", files={"photo": ("p.jpg", b"\xff\xd8photo", "image/jpeg")})
    assert r.status_code == 200, r.text
    return r.json()["photo_id"]


# ---------------------------------------------------------------- 1) photo only where the placement has one


async def test_photo_on_119_mixed_ads_only_reaches_channel_ads(api, tg_mock):
    channel = tg_mock.add_ads("a", 60, "channel_post")
    bots = tg_mock.add_ads("a", 47, "bot_banner")
    await _setup(api, tg_mock)
    rows = (await api.get(f"{A}/ads")).json()["ads"]
    assert len(rows) == 119
    photo_id = await _photo(api)
    assert (await api.post(f"{A}/bulk/capabilities/probe")).json()["edit_photo"] == "yes"
    all_ids = [r["ad_id"] for r in rows]
    bot_ids = {r["ad_id"] for r in rows if r["placement"] == "bot_banner"}
    assert set(bots) <= bot_ids and not set(channel) & bot_ids

    p = (
        await api.post(
            f"{A}/bulk/preview", json={"kind": "content", "ad_ids": all_ids, "mode": "inplace", "photo_id": photo_id}
        )
    ).json()
    assert p["errors"] == [] and p["totals"]["blocked"] == 0
    for it in p["items"]:
        if it["ad_id"] in bot_ids:
            assert it["skipped"] == "رد شد — آگهی ربات عکس ندارد" and not it["changes"]
        else:
            assert [c["field"] for c in it["changes"]] == ["photo"] and not it["skipped"]
    assert p["totals"]["ads"] == 119 - len(bot_ids) and p["totals"]["skipped"] == len(bot_ids)
    assert f"عکس برای {119 - len(bot_ids)} آگهی کانال" in p["photo_summary"]
    assert f"{len(bot_ids)} آگهی ربات بدون تغییر" in p["photo_summary"]

    # Text and photo: bot ads only get the text.
    text = "سایکونت | اینترنت پایدار با تست رایگان 👇"
    p = (
        await api.post(
            f"{A}/bulk/preview",
            json={"kind": "content", "ad_ids": all_ids, "mode": "inplace", "photo_id": photo_id, "text": text},
        )
    ).json()
    assert p["errors"] == [] and p["totals"]["skipped"] == 0
    for it in p["items"]:
        fields = [c["field"] for c in it["changes"]]
        assert fields == (["text"] if it["ad_id"] in bot_ids else ["text", "photo"])
    assert "فقط متنشان عوض می‌شود" in p["photo_summary"]

    # Running it: bot ads are edited with the text only (no photo_id sent to Telegram).
    some = [bots[0], channel[0]]
    op = (
        await api.post(
            f"{A}/bulk",
            json={
                "kind": "content",
                "ad_ids": some,
                "mode": "inplace",
                "photo_id": photo_id,
                "text": text,
                "confirm": True,
            },
        )
    ).json()
    await _drain()
    assert "photo" not in tg_mock.accounts["a"]["ads"][bots[0]]
    assert tg_mock.accounts["a"]["ads"][bots[0]]["text"] == text
    assert tg_mock.accounts["a"]["ads"][channel[0]]["photo"]["photo_id"] == photo_id
    assert {i.status for i in await _items(op["id"])} == {"in_review"}


def test_supports_photo_is_one_rule():
    from app.models import Ad
    from app.services.placement import kind_supports_photo, supports_photo

    assert supports_photo(Ad(placement="channel_post", target_type="channels"))
    assert not supports_photo(Ad(placement="bot_banner", target_type="bots"))
    assert not supports_photo(Ad(placement="", target_type="bots"))
    assert supports_photo(Ad(placement="", target_type="channels"))
    assert kind_supports_photo("channel") and not kind_supports_photo("bot")


# ---------------------------------------------------------------- 2) minimum initial budget and waves


async def test_no_ad_with_less_than_the_minimum_budget(api, tg_mock):
    import json as _json

    tg_mock.set_balance("a", 10)
    await make_account(api)
    form = {
        "kind": "channel",
        "username": "@newvpnchannel",
        "text": "سایکونت | تست رایگان 👇",
        "cpm": 0.45,
        "budget": 0.5,
    }
    r = await api.post(f"{A}/ads", data={"payload": _json.dumps(form)})
    assert r.status_code == 400 and "دست‌کم 1" in r.json()["detail"]
    assert not any(m == "createAd" for _, m in tg_mock.calls)  # refused before asking Telegram
    r = await api.post(f"{A}/ads", data={"payload": _json.dumps({**form, "budget": 1})})
    assert r.status_code == 201
    assert (await api.patch(A, json={"settings": {"min_initial_budget": 0}})).status_code == 400
    await _sync_all()
    p = (
        await api.post(
            f"{A}/bulk/preview",
            json={
                "kind": "content",
                "ad_ids": [3],
                "text": "سایکونت | VPN سریع 👇",
                "budget_source": "account",
                "initial_budget": 0.5,
            },
        )
    ).json()
    assert any("دست‌کم 1" in e for e in p["errors"])


def test_budget_minimum_errors_are_translated():
    from app.integrations.telegram_ads import TelegramAdsError

    for code in ("BUDGET_TOO_LOW", "AD_BUDGET_MIN", "INITIAL_BUDGET_TOO_SMALL"):
        assert "حداقل" in TelegramAdsError("createAd", code).friendly


async def test_replacement_waves_with_a_small_balance(api, tg_mock):
    await _setup(api, tg_mock, balance=1.5)
    body = {
        "kind": "content",
        "ad_ids": [3, 8, 6],
        "mode": "replace",
        "text": "سایکونت | VPN سریع 👇",
        "budget_source": "transfer",
    }
    p = (await api.post(f"{A}/bulk/preview", json=body)).json()
    assert not p["errors"], p["errors"]
    assert p["budget_line"].startswith("برای ساخت 3 آگهی جدید، 3 × 1 = 3 TON لازم است؛ موجودی فعلی 1.5 TON است")
    assert p["totals"]["first_wave"] == 1 and any("موج‌به‌موج" in w for w in p["warnings"])
    assert p["totals"]["new_budget_cap_left"] == 20.0

    op_id = (await api.post(f"{A}/bulk", json={**body, "confirm": True})).json()["id"]
    await _drain()
    items = await _items(op_id)
    assert [i.status for i in items] == ["in_review", "pending", "pending"]
    op = (await api.get(f"{A}/bulk/{op_id}")).json()
    assert op["note"].startswith("موج 1") and op["params"]["waiting_balance"] is True

    for _ in range(3):  # Telegram approves, old ads come back, the next wave goes out
        tg_mock.review("a")
        await sync.run(await _id(), "fast")
        await _drain()
    items = await _items(op_id)
    assert [i.status for i in items] == ["retired", "retired", "retired"], [(i.status, i.step, i.error) for i in items]
    assert [i.params["wave"] for i in items] == [1, 2, 2]
    async with session_scope() as s:
        jobs = (await s.execute(select(Job))).scalars().all()
        creates = [j for j in jobs if j.kind == "create_ad" and j.status == "done"]
        assert len(creates) == 3 and all(j.amount >= 1.0 for j in creates)
        assert all(j.status != "failed" for j in jobs), [(j.kind, j.result) for j in jobs if j.status == "failed"]
        transfers = [j for j in jobs if j.kind == "transfer"]
        assert sorted(round(j.amount, 5) for j in transfers) == [0.2, 1.0, 1.0]  # excess over 1 TON, one top-up max
        acc = (await s.execute(select(Account))).scalar_one()
        assert await actions.topups_last_24h(s, acc) == 0  # replacements leave the top-up cap alone
        assert await actions.new_budget_last_24h(s, acc) == 3.0


async def test_preview_without_balance_for_even_one_ad_and_net_extra_cost(api, tg_mock):
    await _setup(api, tg_mock, balance=0.5)
    body = {"kind": "content", "ad_ids": [3], "mode": "replace", "text": "سایکونت | VPN سریع 👇"}
    p = (await api.post(f"{A}/bulk/preview", json=body)).json()
    assert any("حتی برای ساخت یک آگهی" in e for e in p["errors"])
    tg_mock.set_balance("a", 5)
    await sync.run(await _id(), "fast")
    p = (await api.post(f"{A}/bulk/preview", json={**body, "ad_ids": [2]})).json()  # old budget 0.05
    assert p["items"][0]["money"]["net_extra"] == 0.95
    assert any("هزینه‌ی خالص اضافه" in w for w in p["warnings"])


# ---------------------------------------------------------------- 3) shared and random referral links


async def test_shared_link_even_when_the_referrer_is_already_used(api, tg_mock):
    await _setup(api, tg_mock)
    await api.post(f"{A}/tags", json={"name": "الف", "referrer_id": "9001", "start_code": "ref_a1"})
    await api.patch(A, json={"settings": {"referrer_pool": ["9001"]}})
    await sync.run(await _id(), "fast")
    pool = {"kind": "referral", "ad_ids": [4, 5, 6], "mode": "inplace", "referral_mode": "pool"}
    p = (await api.post(f"{A}/bulk/preview", json=pool)).json()
    assert any("معرف آزادی" in e for e in p["errors"])  # the pool still wants a free referrer
    shared = {**pool, "referral_mode": "shared", "shared_referrer_id": "9001"}
    p = (await api.post(f"{A}/bulk/preview", json=shared)).json()
    assert not p["errors"] and any("کل این گروه" in w for w in p["warnings"])
    assert all(i["changes"][0]["new"] == "https://t.me/psychonet_test_bot?start=ref_a1" for i in p["items"])
    bad = (await api.post(f"{A}/bulk/preview", json={**shared, "shared_referrer_id": "123456"})).json()
    assert any("کاربر نیست" in e for e in bad["errors"])
    op = (await api.post(f"{A}/bulk", json={**shared, "confirm": True})).json()
    await _drain()
    assert {tg_mock.accounts["a"]["ads"][i]["promote_url"] for i in (4, 5, 6)} == {
        "https://t.me/psychonet_test_bot?start=ref_a1"
    }
    tg_mock.review("a")
    await sync.run(await _id(), "fast")
    rows = {a["ad_id"]: a for a in (await api.get(f"{A}/ads")).json()["ads"]}
    assert {rows[i]["tag_name"] for i in (1, 4, 5, 6)} == {"الف"}
    assert op["id"]


async def test_random_links_one_code_per_ad(api, tg_mock, monkeypatch):
    await _setup(api, tg_mock)
    seq = iter(["9001", "9934521877", "9934521877", "9911111111", "9922222222"])
    monkeypatch.setattr(tracking, "random_code", lambda: next(seq))
    body = {"kind": "referral", "ad_ids": [4, 5, 6], "mode": "inplace", "referral_mode": "random"}
    p = (await api.post(f"{A}/bulk/preview", json=body)).json()
    assert not p["errors"] and any("هنوز معلوم نیست ربات" in w for w in p["warnings"])
    op = (await api.post(f"{A}/bulk", json={**body, "confirm": True})).json()
    await _drain()
    urls = [tg_mock.accounts["a"]["ads"][i]["promote_url"] for i in (4, 5, 6)]
    codes = [u.split("start=")[1] for u in urls]
    # 9001 is a real user of the bot and the duplicate is skipped
    assert codes == ["9934521877", "9911111111", "9922222222"]
    async with session_scope() as s:
        tags = (await s.execute(select(ReferralTag).where(ReferralTag.kind == "random"))).scalars().all()
        assert sorted(t.start_code for t in tags) == sorted(codes) and all(t.referrer_id == t.start_code for t in tags)
    tg_mock.review("a")
    await sync.run(await _id(), "fast")
    rows = {a["ad_id"]: a for a in (await api.get(f"{A}/ads")).json()["ads"]}
    assert all(rows[i]["tag_kind"] == "random" for i in (4, 5, 6)) and len({rows[i]["tag_id"] for i in (4, 5, 6)}) == 3
    assert {i.status for i in await _items(op["id"])} == {"approved"}


async def test_random_code_tracking_test(api, bot_mock):
    await make_account(api)
    t = (await api.post(f"{A}/tracking/random/test")).json()["test"]
    assert t["link"] == f"https://t.me/psychonet_test_bot?start={t['code']}" and len(t["code"]) == 10
    assert (await api.post(f"{A}/tracking/random/check")).json()["test"]["result"] == "unknown"
    bot_mock.start("a", t["code"])  # the bot ignores unknown codes
    r = (await api.post(f"{A}/tracking/random/check")).json()
    assert r["test"]["result"] == "ignored" and r["random"]["result"] == "ignored"
    assert (await api.get(A)).json()["settings"]["random_tracking"] == "ignored"

    bot_mock.stores_unknown_start = True
    t = (await api.post(f"{A}/tracking/random/test")).json()["test"]
    bot_mock.start("a", t["code"])
    r = (await api.post(f"{A}/tracking/random/check")).json()
    assert r["test"]["result"] == "works" and r["random"]["result"] == "works"
    p = (
        await api.post(
            f"{A}/bulk/preview", json={"kind": "referral", "ad_ids": [4], "mode": "inplace", "referral_mode": "random"}
        )
    ).json()
    assert not any("هنوز معلوم نیست ربات" in w for w in p["warnings"])


async def test_random_tags_attribute_sales_when_the_bot_records_codes(api, tg_mock, bot_mock, monkeypatch):
    bot_mock.stores_unknown_start = True
    await _setup(api, tg_mock)
    monkeypatch.setattr(tracking, "random_code", iter(["9944444444"]).__next__)
    await api.post(
        f"{A}/bulk",
        json={"kind": "referral", "ad_ids": [4], "mode": "inplace", "referral_mode": "random", "confirm": True},
    )
    await _drain()
    bot_mock.start("a", "9944444444")
    bot_mock.start("a", "9944444444")
    await sync.run(await _id(), "bot")
    tag = next(t for t in (await api.get(f"{A}/tags")).json()["tags"] if t["start_code"] == "9944444444")
    assert tag["kind"] == "random" and tag["users"] == 2


# ---------------------------------------------------------------- 4) ad numbers per period


async def test_ad_numbers_per_period(api, tg_mock):
    await make_account(api)
    await _sync_all()
    r = (await api.get(f"{A}/ads?period=today")).json()
    assert r["period"]["preset"] == "today" and r["period"]["source"] == "baseline"
    assert all(a["period_stats"]["spent"] == 0 for a in r["ads"])  # baselines taken at the first sync
    raw = dict(tg_mock.accounts["a"]["ads"][1])
    tg_mock.set_ad(
        "a",
        1,
        views=raw["views"] + 500,
        clicks=raw["clicks"] + 20,
        actions=raw["actions"] + 7,
        spent_budget=raw["spent_budget"] + 0.03,
    )
    await sync.run(await _id(), "fast")
    rows = {a["ad_id"]: a for a in (await api.get(f"{A}/ads?period=today")).json()["ads"]}
    assert rows[1]["period_stats"] == {"views": 500, "clicks": 20, "actions": 7, "spent": 0.03}
    assert rows[3]["period_stats"]["views"] == 0
    all_time = {a["ad_id"]: a for a in (await api.get(f"{A}/ads?period=all")).json()["ads"]}
    assert all_time[1]["period_stats"]["views"] == raw["views"] + 500
    week = (await api.get(f"{A}/ads?period=7d")).json()
    assert week["period"]["source"] == "daily" and sum(a["period_stats"]["spent"] for a in week["ads"]) > 0
    custom = (await api.get(f"{A}/ads?period=custom&from=2000-01-01&to=2000-01-31")).json()
    assert custom["period"]["from"] == "2000-01-01" and all(a["period_stats"]["views"] == 0 for a in custom["ads"])
    assert (await api.get(f"{A}/ads?period=nope")).json()["period"]["preset"] == "7d"


# ---------------------------------------------------------------- 5) dashboard: today


def test_today_starts_at_tehran_midnight_and_compares_with_yesterday_so_far():
    tz = ZoneInfo("Asia/Tehran")
    now = int(datetime(2026, 10, 25, 21, 0, tzinfo=UTC).timestamp())  # 00:30 on Oct 26 in Tehran
    midnight, prev_midnight, prev_now = periods.day_bounds(tz, now)
    assert datetime.fromtimestamp(midnight, UTC) == datetime(2026, 10, 25, 20, 30, tzinfo=UTC)
    assert datetime.fromtimestamp(prev_midnight, UTC) == datetime(2026, 10, 24, 20, 30, tzinfo=UTC)
    assert datetime.fromtimestamp(prev_now, UTC) == datetime(2026, 10, 24, 21, 0, tzinfo=UTC)
    before = int(datetime(2026, 10, 25, 20, 29, tzinfo=UTC).timestamp())  # 23:59 Tehran, Oct 25
    assert periods.day_bounds(tz, before)[0] == prev_midnight


async def test_dashboard_today_window(api):
    import time as _time

    await make_account(api)
    aid = await _id()
    now = int(_time.time())
    midnight, prev_midnight, prev_now = periods.day_bounds(ZoneInfo("Asia/Tehran"), now)
    snaps = [
        (prev_midnight - 60, 10.0, 1000, 100, 10),  # just before yesterday's midnight
        (prev_now - 30, 10.4, 1400, 140, 14),  # yesterday at this hour
        (midnight - 300, 11.0, 2000, 200, 20),  # end of yesterday
        (midnight + 1, 11.0, 2000, 200, 20),
        (now - 5, 11.25, 2500, 260, 25),
    ]
    async with session_scope() as s:
        for ts, spent, views, clicks, actions_ in snaps:
            if ts > now:
                continue
            s.add(
                AccountSnapshot(account_id=aid, ts=ts, spent_budget=spent, views=views, clicks=clicks, actions=actions_)
            )
    d = (await api.get(f"{A}/dashboard")).json()["summary"]
    today = d["today"]
    assert today["spent"] == 0.25 and today["views"] == 500 and today["clicks"] == 60 and today["starts"] == 5
    assert today["prev_spent"] == 0.4 and today["prev_starts"] == 4  # yesterday until the same hour, not all day
    assert today["since_ts"] == midnight and today["tz"] == "Asia/Tehran"
    assert set(d["7"]) <= set(today) and "attributed_today" in d
    async with session_scope() as s:
        assert (await s.execute(select(BulkItem))).first() is None
