"""v2.2.4: the source checked against the Telegram Ads API docs.

- A replacement ad reaches the same audience: the whole AdTarget (languages, topics, excluded channels, user
  demographics, audiences, search queries) becomes the InputAdTarget of createAd.
- A replacement keeps the old ad's photo/video, frequency, daily limit, schedule and website settings.
- Money is rounded down to the precision of the account currency ("Currencies"), since Telegram rounds.
- CHANNEL_ID_UNKNOWN / BOT_ID_UNKNOWN ("Resolving channels and bots") and the access_token parameter."""

import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.session import session_scope
from app.integrations.telegram_ads import TelegramAdsApi, TelegramAdsClient, TelegramAdsError, floor_amount
from app.models import Account, Ad, Job
from app.services import actions, bulk
from tests.conftest import TG_BASE
from tests.test_v21 import A, _drain, _items, _setup, _sync_all

TEXT = "سایکونت | VPN سریع 👇"


def _ad(target: dict, placement: str = "channel_post", **kw) -> SimpleNamespace:
    return SimpleNamespace(target=target, target_type=target.get("type", ""), placement=placement, **kw)


@pytest.fixture
def tg(tg_mock, http_mocks) -> TelegramAdsApi:
    tg_mock.set_balance("a", 100)
    return TelegramAdsApi("mocktoken-a", base=TG_BASE)


async def _create(tg: TelegramAdsApi, target: dict, placement: str, **kw) -> dict:
    params = {
        "title": "t",
        "text": TEXT,
        "promote_url": "https://t.me/psychonet_test_bot",
        "cpm": 0.5,
        "placement": placement,
        "target": target,
        "return_target": True,
    } | kw
    return await tg.create_ad(f"k-{time.time_ns()}", **params)


# ---------------------------------------------------------------- A) targets


def test_channel_target_keeps_languages_topics_and_exclusions():
    target = {
        "type": "channels",
        "languages": [{"language_code": "fa", "name": "Persian"}],
        "topics": [{"topic_id": 1, "name": "Technology"}],
        "exclude_topics": [{"topic_id": 3, "name": "Crypto"}],
        "channels": [],
        "exclude_channels": [{"channel_id": 77, "username": "spam_news"}, {"channel_id": 78}],
    }
    out, slot = bulk.create_target(_ad(target))  # type: ignore[misc]
    assert slot == "channel_post"
    assert out == {
        "type": "channels",
        "language_codes": ["fa"],
        "topic_ids": [1],
        "exclude_topic_ids": [3],
        "exclude_channel_ids": ["@spam_news", 78],
    }


def test_channel_without_username_goes_by_numeric_id_and_unnamed_ones_block():
    ok = {"type": "channels", "channels": [{"channel_id": 5, "username": "a_b_c"}, {"channel_id": 6}]}
    assert bulk.create_target(_ad(ok))[0]["channel_ids"] == ["@a_b_c", 6]  # type: ignore[index]
    # A channel we cannot name would silently drop out: no replacement rather than a different audience.
    assert bulk.create_target(_ad({"type": "channels", "channels": [{"title": "?"}]})) is None
    assert bulk.create_target(_ad({"type": "channels"})) is None


def test_user_target_is_copied_field_by_field():
    target = {
        "type": "users",
        "countries": [{"country_code": "IR", "name": "Iran"}],
        "locations": [{"location_id": 101, "name": "Tehran"}],
        "languages": [{"language_code": "fa"}],
        "topics": [{"topic_id": 1}, {"topic_id": 2}],
        "intersect_topics": True,
        "exclude_topics": [{"topic_id": 3}],
        "channels": [{"channel_id": 9, "username": "tech_news"}],
        "exclude_channels": [{"channel_id": 10, "username": "old_news"}],
        "audiences": [{"audience_id": 4, "title": "buyers"}],
        "exclude_audiences": [{"audience_id": 5, "title": "old"}],
        "device": "android",
        "exclude_political_channels": True,
    }
    out, slot = bulk.create_target(_ad(target, placement=""))  # type: ignore[misc]
    assert slot == "channel_post"
    assert out == {
        "type": "users",
        "country_codes": ["IR"],
        "location_ids": [101],
        "language_codes": ["fa"],
        "topic_ids": [1, 2],
        "intersect_topics": True,
        "exclude_topic_ids": [3],
        "channel_ids": ["@tech_news"],
        "exclude_channel_ids": ["@old_news"],
        "audience_ids": [4],
        "exclude_audience_ids": [5],
        "device": "android",
        "exclude_political_channels": True,
    }


def test_bot_and_search_targets():
    bots = {"type": "bots", "bots": [{"bot_id": 1, "username": "some_bot"}, {"bot_id": 2}]}
    assert bulk.create_target(_ad(bots, "bot_banner")) == ({"type": "bots", "bot_ids": ["@some_bot", 2]}, "bot_banner")
    search = {"type": "search", "search_queries": ["vpn", "proxy"]}
    assert bulk.create_target(_ad(search, "")) == (search, "search_result")
    assert bulk.create_target(_ad({"type": "search", "search_queries": []}, "")) is None
    assert bulk.create_target(_ad({"type": "something_new"})) is None


@pytest.mark.parametrize(
    ("target", "placement"),
    [
        (
            {
                "type": "channels",
                "language_codes": ["fa"],
                "topic_ids": [1, 2],
                "exclude_topic_ids": [3],
                "exclude_channel_ids": ["@spam_news"],
            },
            "channel_post",
        ),
        ({"type": "channels", "channel_ids": ["@tech_news", "@daily_news"]}, "channel_post"),
        (
            {
                "type": "users",
                "country_codes": ["IR"],
                "location_ids": [101, 102],
                "language_codes": ["fa", "en"],
                "topic_ids": [1],
                "intersect_topics": True,
                "exclude_topic_ids": [4],
                "channel_ids": ["@tech_news"],
                "exclude_channel_ids": ["@old_news"],
                "device": "ios",
                "political_channels_only": True,
            },
            "channel_post",
        ),
        ({"type": "bots", "bot_ids": ["@some_bot"]}, "bot_banner"),
        ({"type": "search", "search_queries": ["vpn", "free proxy"]}, "search_result"),
    ],
)
async def test_every_target_survives_a_round_trip_through_telegram(tg, target, placement):
    """What Telegram returns (AdTarget), turned back into createAd input, gives the very same AdTarget."""
    first = await _create(tg, target, placement)
    again, slot = bulk.create_target(_ad(first["target"], first["placement"]))  # type: ignore[misc]
    second = await _create(tg, again, slot)
    assert second["target"] == first["target"]
    assert second["placement"] == placement


async def test_audiences_survive_the_round_trip(tg):
    aud = (await tg.create_audience("buyers", key="aud-1"))["audience_id"]
    first = await _create(tg, {"type": "users", "country_codes": [], "audience_ids": [aud]}, "channel_post")
    assert first["target"]["audiences"][0]["audience_id"] == aud
    again, slot = bulk.create_target(_ad(first["target"]))  # type: ignore[misc]
    assert again["audience_ids"] == [aud] and again["country_codes"] == []
    assert (await _create(tg, again, slot))["target"] == first["target"]


async def test_bulk_replace_of_a_topic_targeted_ad_keeps_its_audience(api, tg_mock):
    await _setup(api, tg_mock)
    topics = {
        "type": "channels",
        "languages": [{"language_code": "fa", "name": "Persian"}],
        "topics": [{"topic_id": 1, "name": "Technology"}],
        "exclude_topics": [],
        "channels": [],
        "exclude_channels": [{"channel_id": 1, "username": "spam_news", "title": "spam"}],
    }
    tg_mock.set_ad("a", 3, target=topics)
    await _sync_all()  # the new target
    body = {"kind": "content", "ad_ids": [3], "mode": "replace", "text": TEXT}
    preview = (await api.post(f"{A}/bulk/preview", json=body)).json()
    assert not preview["items"][0].get("blocked"), preview["items"][0]
    op_id = (await api.post(f"{A}/bulk", json=body | {"confirm": True})).json()["id"]
    await _drain()
    new = tg_mock.accounts["a"]["ads"][(await _items(op_id))[0].new_ad_id]
    assert [x["language_code"] for x in new["target"]["languages"]] == ["fa"]
    assert [x["topic_id"] for x in new["target"]["topics"]] == [1]
    assert [x["username"] for x in new["target"]["exclude_channels"]] == ["spam_news"]


# ---------------------------------------------------------------- B) media and settings


def test_copy_from_old_keeps_media_and_settings():
    now = int(time.time())
    old = {
        "photo": {"photo_id": "p1", "photo_url": "x"},
        "impression_frequency": 3,
        "website_name": "Psychonet",
        "website_photo": {"photo_id": "w1"},
        "button": "open",
        "additional_info": "",
        "show_userpic": True,
        "daily_budget_limit": 2.5,
        "schedule": {"week_hours_mask": [16777215, 0, 0, 0, 0, 0, 255], "timezone": 12600},
        "activate_date": now - 3600,
        "deactivate_date": now + 86400,
    }
    site = {"placement": "channel_post", "promote_url": "https://psychonet.example/vpn"}
    out = bulk.copy_from_old(old, site, now)
    assert out == {
        "photo_id": "p1",
        "impression_frequency": 3,
        "website_name": "Psychonet",
        "button": "open",
        "show_userpic": True,
        "website_photo_id": "w1",
        "daily_budget_limit": 2.5,
        "schedule": {"week_hours_mask": [16777215, 0, 0, 0, 0, 0, 255], "timezone": 12600},
        "deactivate_date": now + 86400,
    }
    # A t.me link takes no website settings (createAd refuses them there).
    bot_link = bulk.copy_from_old(old, {"placement": "channel_post", "promote_url": "https://t.me/x_bot"}, now)
    assert {"website_name", "button", "website_photo_id"}.isdisjoint(bot_link) and bot_link["photo_id"] == "p1"
    # A video banner keeps its video.
    vb = bulk.copy_from_old({"video": {"video_id": "v9"}}, {"placement": "video_banner"}, now)
    assert vb == {"video_id": "v9"}
    # A new photo wins; a video is kept when there is no photo; a bot banner carries no media.
    assert "photo_id" not in bulk.copy_from_old(old, {"placement": "channel_post", "photo_id": "new"}, now)
    vid = bulk.copy_from_old({"video": {"video_id": "v1"}}, {"placement": "channel_post"}, now)
    assert vid == {"video_id": "v1"}
    assert bulk.copy_from_old(old | {"photo": None}, {"placement": "bot_banner"}, now).get("photo_id") is None
    assert "schedule" not in bulk.copy_from_old({"schedule": {"week_hours_mask": [0] * 7}}, {}, now)


async def test_replacement_keeps_the_old_photo_and_settings(api, tg_mock, http_mocks):
    await _setup(api, tg_mock)
    tg = TelegramAdsApi("mocktoken-a", base=TG_BASE)
    photo = (await tg.upload_photo("p.png", b"\x89PNG-old", "image/png"))["photo_id"]
    sched = {"week_hours_mask": [255] * 7, "timezone": 12600}
    tg_mock.set_ad(
        "a", 3, photo={"photo_id": photo, "photo_url": "x"}, impression_frequency=2, show_userpic=True, schedule=sched
    )
    await _sync_all()
    body = {"kind": "content", "ad_ids": [3], "mode": "replace", "text": TEXT}
    preview = (await api.post(f"{A}/bulk/preview", json=body)).json()
    assert any("روی آگهی جدید گذاشته می‌شود" in w for w in preview["items"][0]["warnings"])
    op_id = (await api.post(f"{A}/bulk", json=body | {"confirm": True})).json()["id"]
    await _drain()
    item = (await _items(op_id))[0]
    new = tg_mock.accounts["a"]["ads"][item.new_ad_id]
    assert new["photo"]["photo_id"] == photo
    assert new["impression_frequency"] == 2 and new.get("show_userpic") is True
    assert new["schedule"]["week_hours_mask"] == [255] * 7
    async with session_scope() as s:
        job = (await s.execute(select(Job).where(Job.kind == "create_ad"))).scalars().first()
        assert job is not None and job.params["photo_id"] == photo and job.params["_copied"] is True
        row = (await s.execute(select(Ad).where(Ad.ad_id == item.new_ad_id))).scalar_one()
        assert row.has_photo


async def test_a_retried_create_sends_the_same_parameters(api, tg_mock):
    """The old ad is read once: a retry after a lost answer replays under the same key, no PARAM_MISMATCH."""
    await _setup(api, tg_mock)
    body = {"kind": "content", "ad_ids": [3], "mode": "replace", "text": TEXT, "confirm": True}
    op_id = (await api.post(f"{A}/bulk", json=body)).json()["id"]
    real = TelegramAdsClient.create_ad
    lost = {"n": 0}

    async def flaky(self, key, **params):
        res = await real(self, key, **params)
        if lost["n"] == 0:
            lost["n"] += 1
            tg_mock.set_ad("a", 3, impression_frequency=4)  # the old ad changes between the two tries
            raise TelegramAdsError("createAd", "network: lost answer")
        return res

    TelegramAdsClient.create_ad = flaky  # type: ignore[method-assign]
    try:
        await _drain()
    finally:
        TelegramAdsClient.create_ad = real  # type: ignore[method-assign]
    item = (await _items(op_id))[0]
    assert item.new_ad_id and item.status in ("in_review", "approved", "retired"), item.error
    created = [k for k, m in tg_mock.calls if m == "createAd"]
    assert len(created) == 2  # one lost, one replayed
    assert "impression_frequency" not in tg_mock.accounts["a"]["ads"][item.new_ad_id]


# ---------------------------------------------------------------- C) currency precision


def test_floor_amount_by_places():
    assert floor_amount(1.234567) == 1.23456
    assert floor_amount(1.2399, 3) == 1.239
    assert floor_amount(1.99, 0) == 1.0
    c = TelegramAdsClient("t", currency="XTR")
    assert c.amount(10.98765) == 10.987 and c.budget(10.9) == 10.0
    t = TelegramAdsClient("t", currency="TON")
    assert t.amount(1.234567) == 1.23456 and t.budget(1.239) == 1.23
    assert TelegramAdsClient("t").budget(1.239) == 1.23  # unknown currency: TON/EUR precision


async def test_stars_account_never_asks_for_a_rounded_up_budget(tg_mock, http_mocks):
    """Telegram rounds 1.6 XTR to 2: more than the balance. The client sends 1 instead."""
    tg_mock.accounts["a"]["account"]["currency"] = "XTR"
    tg_mock.set_balance("a", 1.6)
    plain = TelegramAdsApi("mocktoken-a", base=TG_BASE)
    target = {"type": "channels", "channel_ids": ["@tech_news"]}
    params = {"title": "t", "text": TEXT, "promote_url": "https://t.me/x", "cpm": 1, "target": target}
    with pytest.raises(TelegramAdsError) as e:
        await plain.call("createAd", idempotency_key="x1", initial_budget=1.6, **params)
    assert e.value.error == "NOT_ENOUGH_BUDGET"
    stars = TelegramAdsApi("mocktoken-a", base=TG_BASE, currency="XTR")
    ad = await stars.create_ad("x2", initial_budget=1.6, **params)
    assert ad["remaining_budget"] == 1.0 and ad["currency"] == "XTR"


async def test_stars_withdrawal_is_rounded_down_to_three_places(api, tg_mock):
    await _setup(api, tg_mock)
    async with session_scope() as s:
        acc = (await s.execute(select(Account))).scalars().first()
        assert acc is not None
        acc.currency = "XTR"
    tg_mock.accounts["a"]["account"]["currency"] = "XTR"
    tg_mock.set_ad("a", 3, remaining_budget=2.12399, status="stopped", is_paused=True)
    tg_mock.inactive_seconds = 0
    async with session_scope() as s:
        acc = (await s.execute(select(Account))).scalars().first()
        assert acc is not None
        actions.enqueue(s, acc, "withdraw", 3, 0, actor="user")
    await _drain()
    async with session_scope() as s:
        job = (await s.execute(select(Job).where(Job.kind == "withdraw"))).scalars().one()
    assert job.status == "done", job.result
    assert job.params["done_amount"] == 2.123
    assert tg_mock.accounts["a"]["ads"][3]["remaining_budget"] == pytest.approx(0.00099)
