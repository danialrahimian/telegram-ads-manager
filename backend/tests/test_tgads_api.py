"""The whole Telegram Ads API documentation, rule by rule, through the panel's client (TelegramAdsApi).

Each section follows the documentation: making requests, idempotent requests, then every method in the
order of "Available methods". The fake API (tests/mocks/tgads_api.py) implements what the document says;
these tests pin both the client's requests and those documented behaviours.
"""

import hashlib
import time

import httpx
import pytest

from app.integrations.telegram_ads import TelegramAdsApi, TelegramAdsError, clip_bytes, flat
from tests.conftest import TG_BASE

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 64
FA_TEXT = "سایکونت | اینترنت پایدار با تست رایگان 👇"


@pytest.fixture
def tg(tg_mock, http_mocks) -> TelegramAdsApi:
    tg_mock.supports_delete = True
    tg_mock.set_balance("a", 100)
    return TelegramAdsApi("mocktoken-a", base=TG_BASE)


async def err(coro) -> str:
    with pytest.raises(TelegramAdsError) as e:
        await coro
    return e.value.error


def raw(method: str, *, token: str = "mocktoken-a", get: bool = False, **kw) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    headers.update(kw.pop("headers", {}))
    with httpx.Client() as c:
        if get:
            return c.get(f"{TG_BASE}/{method}", headers=headers, **kw)
        return c.post(f"{TG_BASE}/{method}", headers=headers, **kw)


def channels(*names: str) -> dict:
    return {"type": "channels", "channel_ids": [f"@{n}" for n in names or ("tech_news",)]}


async def new_ad(tg: TelegramAdsApi, key: str = "", **kw) -> dict:
    params = {
        "title": "Test ad",
        "text": FA_TEXT,
        "promote_url": "https://t.me/psychonet_test_bot",
        "cpm": 0.5,
        "placement": "channel_post",
        "target": channels(),
        "initial_budget": 1,
        "return_target": True,
    } | kw
    return await tg.create_ad(key or f"k-{time.time_ns()}", **{k: v for k, v in params.items() if v is not None})


# ================================================================ making requests


async def test_parameters_in_json_form_or_query_string_give_the_same_answer(tg):
    for fmt in ("json", "form", "query"):
        got = await tg.call("getAdsById", fmt=fmt, ad_ids=[1, 2], return_target=True)
        assert [a["ad_id"] for a in got] == [1, 2] and all("target" in a for a in got)


def test_get_and_post_and_method_names_are_case_insensitive(tg):
    for name in ("getCurrentAccount", "getcurrentaccount", "GETCURRENTACCOUNT"):
        assert raw(name, get=True).json()["result"]["account_id"] == "mock-a"
        assert raw(name, json={}).json()["ok"] is True


def test_query_string_values_with_a_post(tg):
    r = raw("getAdsById", params={"ad_ids": "[3]"}, json={})
    assert [a["ad_id"] for a in r.json()["result"]] == [3]


def test_ok_false_with_an_error_field(tg):
    body = raw("noSuchMethod", json={}).json()
    assert body == {"ok": False, "error": "METHOD_NOT_FOUND"}
    assert raw("getCurrentAccount", token="").json()["error"] == "ACCESS_TOKEN_REQUIRED"
    assert raw("getCurrentAccount", token="wrong").json()["error"] == "ACCESS_TOKEN_INVALID"


async def test_unicode_text_survives_every_format(tg):
    for fmt in ("json", "form", "query"):
        ad = await new_ad(tg)
        out = await tg.edit_ad(ad["ad_id"], fmt=fmt, text=FA_TEXT + " " + fmt)
        assert out["text"] == FA_TEXT + " " + fmt
        assert (await tg.ads_by_id([ad["ad_id"]]))[ad["ad_id"]]["text"] == FA_TEXT + " " + fmt


def test_lists_and_booleans_are_json_serialized_outside_json_bodies():
    assert flat([1, 2]) == "[1, 2]"
    assert flat(True) == "true" and flat(False) == "false"
    assert flat({"type": "bots"}) == '{"type": "bots"}'
    assert flat("سلام") == "سلام"


async def test_the_client_sends_edits_in_the_format_it_is_told(tg, tg_mock):
    tg_mock.ignored_formats = {"json"}
    ad = await new_ad(tg)
    assert (await tg.edit_ad(ad["ad_id"], text="A new text"))["text"] == FA_TEXT  # json ignored
    tg.edit_format = "form"
    assert (await tg.edit_ad(ad["ad_id"], text="A new text"))["text"] == "A new text"


# ================================================================ idempotent requests


async def test_same_key_replays_the_first_answer_without_doing_it_twice(tg, tg_mock):
    before = tg_mock.accounts["a"]["account"]["remaining_budget"]
    a1 = await new_ad(tg, key="same-key")
    a2 = await new_ad(tg, key="same-key")
    assert a1 == a2
    assert tg_mock.accounts["a"]["account"]["remaining_budget"] == pytest.approx(before - 1)


def test_replayed_answer_has_the_header_and_key_may_be_a_parameter(tg):
    body = {
        "title": "x",
        "text": "t",
        "promote_url": "https://t.me/x_bot",
        "cpm": 1,
        "target": {"type": "bots", "bot_ids": ["@x_bot"]},
        "idempotency_key": "param-key",
    }
    first = raw("createAd", json=body)
    again = raw("createAd", json=body)
    assert first.json() == again.json() and first.json()["ok"]
    assert "Idempotent-Replayed" not in first.headers and again.headers["Idempotent-Replayed"] == "true"
    body.pop("idempotency_key")
    header = raw("createAd", json=body, headers={"Idempotency-Key": "param-key"})
    assert header.headers.get("Idempotent-Replayed") == "true"  # one key, header or parameter


async def test_key_with_other_parameters_is_refused(tg):
    await new_ad(tg, key="k-mismatch")
    assert await err(new_ad(tg, key="k-mismatch", cpm=0.9)) == "IDEMPOTENT_PARAM_MISMATCH"


def test_key_still_running_and_key_too_long(tg, tg_mock):
    tg_mock.in_progress_keys.add("busy")
    r = raw("increaseAdBudget", json={"ad_id": 3, "amount": 1}, headers={"Idempotency-Key": "busy"})
    assert r.json()["error"] == "IDEMPOTENT_REQUEST_IN_PROGRESS"
    r = raw("increaseAdBudget", json={"ad_id": 3, "amount": 1}, headers={"Idempotency-Key": "k" * 129})
    assert r.json()["error"] == "IDEMPOTENCY_KEY_INVALID"
    r = raw("increaseAdBudget", json={"ad_id": 3, "amount": 1}, headers={"Idempotency-Key": "k" * 128})
    assert r.json()["ok"] is True


async def test_failed_validation_is_not_saved_under_the_key(tg):
    assert await err(new_ad(tg, key="k-retry", title=None)) == "AD_TITLE_REQUIRED"
    ad = await new_ad(tg, key="k-retry")  # same key, now valid: executed, no mismatch
    assert ad["ad_id"]


async def test_an_executed_failure_is_saved_under_the_key(tg, tg_mock):
    tg_mock.set_balance("a", 0)
    assert await err(tg.topup(3, 5, key="k-poor")) == "NOT_ENOUGH_BUDGET"
    tg_mock.set_balance("a", 100)
    assert await err(tg.topup(3, 5, key="k-poor")) == "NOT_ENOUGH_BUDGET"  # replayed, not run again


async def test_keys_are_forgotten_after_24_hours(tg, tg_mock):
    await new_ad(tg, key="k-old")
    tg_mock.accounts["a"]["idem"]["k-old"]["ts"] -= 24 * 3600 + 1
    await new_ad(tg, key="k-old", cpm=0.9)  # no mismatch: the old key is gone


# ================================================================ accounts


ACCOUNT = {
    "title": "Partner",
    "full_name": "Partner LLC",
    "email": "a@b.co",
    "phone_number": "+49123456",
    "country": "Germany",
    "city": "Berlin",
}


async def test_create_account_and_its_info(tg):
    info = await tg.create_account(**ACCOUNT, key="acc-1", legal_name="Partner GmbH")
    assert info["account_id"] and info["title"] == "Partner" and info["legal_name"] == "Partner GmbH"
    assert set(info) >= {"account_id", "title", "full_name", "email", "phone_number", "country", "city"}
    assert (await tg.account_info(account_id=info["account_id"]))["city"] == "Berlin"
    assert (await tg.account_info())["account_id"] == "mock-a"


@pytest.mark.parametrize("field", list(ACCOUNT))
async def test_create_account_needs_every_required_field(tg, field):
    args = ACCOUNT | {field: ""}
    assert (await err(tg.create_account(**args, key=f"acc-{field}"))).endswith("_REQUIRED")


async def test_create_account_lengths_and_email(tg):
    assert (await err(tg.create_account(**(ACCOUNT | {"title": "t" * 129}), key="a1"))).endswith("TOO_LONG")
    assert (await err(tg.create_account(**(ACCOUNT | {"email": "e" * 65}), key="a2"))).endswith("TOO_LONG")
    assert await err(tg.create_account(**(ACCOUNT | {"email": "no-at"}), key="a3")) == "EMAIL_INVALID"
    await tg.create_account(**(ACCOUNT | {"title": "t" * 128}), key="a4")


async def test_edit_account_info(tg):
    rel = await tg.create_account(**ACCOUNT, key="acc-e")
    out = await tg.edit_account_info(account_id=rel["account_id"], title="Renamed", city="Munich")
    assert out["title"] == "Renamed" and out["city"] == "Munich"
    assert (await tg.edit_account_info(country="Iran"))["country"] == "Iran"
    assert await err(tg.edit_account_info(account_id="nope")) == "ACCOUNT_ID_INVALID"


async def test_related_accounts_list_and_budget_transfers(tg, tg_mock):
    r1 = await tg.create_account(**ACCOUNT, key="r1")
    r2 = await tg.create_account(**(ACCOUNT | {"title": "Second"}), key="r2")
    lst = await tg.related_accounts()
    assert lst["total_count"] == 2 and [a["account_id"] for a in lst["accounts"]] == [
        r1["account_id"],
        r2["account_id"],
    ]
    assert len((await tg.related_accounts(offset=1, limit=1))["accounts"]) == 1

    tx = await tg.increase_account_budget(r1["account_id"], 10, key="t1")
    assert tx["status"] == "completed" and tx["amount"] == 10
    assert tx["related_account"]["remaining_budget"] == 10 and tx["main_account"]["remaining_budget"] == 90
    assert (await tg.transaction_status(tx["transaction_id"]))["status"] == "completed"
    back = await tg.decrease_account_budget(r1["account_id"], 4, key="t2")
    assert back["amount"] == -4 and back["related_account"]["remaining_budget"] == 6

    poor = await tg.decrease_account_budget(r2["account_id"], 1, key="t3")
    assert poor["status"] == "failed" and poor["error"] == "NOT_ENOUGH_BUDGET"
    assert await err(tg.transaction_status("tx999")) == "TRANSACTION_ID_INVALID"
    assert await err(tg.increase_account_budget("mock-a", 1, key="t4")) == "ACCOUNT_ID_INVALID"
    assert await err(tg.increase_account_budget(r1["account_id"], 0, key="t5")) == "AMOUNT_INVALID"

    accs = await tg.accounts_by_id(["mock-a", r1["account_id"], "unknown"])
    assert [a["account_id"] for a in accs] == ["mock-a", r1["account_id"]]
    assert set(accs[0]) >= {"account_id", "title", "currency", "spent_budget", "remaining_budget", "ads_budget"}


async def test_account_transactions(tg):
    rel = await tg.create_account(**ACCOUNT, key="rt")
    await tg.increase_account_budget(rel["account_id"], 2, key="rt1")
    await tg.topup(3, 1.5, key="rt2")
    txs = await tg.account_transactions()
    peers = [t["peer"]["type"] for t in txs["transactions"]]
    assert "related_account" in peers and "ad" in peers
    ad_tx = next(t for t in txs["transactions"] if t["peer"]["type"] == "ad")
    assert ad_tx["amount"] == -1.5 and ad_tx["peer"]["ad_id"] == 3 and ad_tx["currency"] == "TON"
    side = await tg.account_transactions(account_id=rel["account_id"])
    assert side["transactions"][0]["peer"] == {"type": "main_account"} and side["transactions"][0]["amount"] == 2
    page = await tg.account_transactions(limit=1)
    assert len(page["transactions"]) == 1 and page["next_offset"] == "1"


async def test_account_stats_and_report(tg):
    now = int(time.time())
    daily = await tg.account_stats(now - 30 * 86400, now, 86400)
    assert daily and all(i["to_time"] - i["from_time"] == 86400 for i in daily)
    assert set(daily[0]) >= {"from_time", "to_time", "views", "opens", "clicks", "actions", "currency", "spent_budget"}
    assert isinstance(await tg.account_stats(now - 3600, now, 300), list)
    assert await err(tg.account_stats(now - 3600, now, 600)) == "INTERVAL_INVALID"
    assert await err(tg.account_stats(now - 1001 * 300, now, 300)) == "PERIOD_TOO_LONG"
    t = time.gmtime()
    report = await tg.account_report(t.tm_year, t.tm_mon)
    assert report and set(report[0]) >= {"ad_id", "ad_title", "views", "clicks", "actions", "spent_budget"}
    assert await err(tg.account_report(2020, 1)) == "YEAR_INVALID"
    assert await err(tg.account_report(2026, 13)) == "MONTH_INVALID"
    assert await tg.account_report(2100, 12) == []


# ================================================================ uploads


async def test_upload_ad_photo(tg):
    assert (await tg.upload_photo("a.jpg", JPEG, "image/jpeg"))["photo_id"].startswith("ph_")
    up = await tg.upload_photo("a.png", PNG, "image/png")
    assert set(up) == {"photo_id", "photo_url"}
    assert await err(tg.upload_photo("a.gif", b"GIF89a....", "image/gif")) == "FILE_TYPE_INVALID"
    assert await err(tg.upload_photo("big.jpg", b"\xff\xd8" + b"0" * (5 * 2**20), "image/jpeg")) == "FILE_TOO_BIG"
    assert await err(tg.call("uploadAdPhoto")) == "FILE_REQUIRED"


async def test_upload_ad_video_and_website_photo(tg):
    v = await tg.upload_video("v.mp4", MP4)
    assert set(v) == {"video_id", "video_url"}
    assert await err(tg.upload_video("v.mp4", b"\x00" * 30 + b"ftyp" + b"0" * (20 * 2**20))) == "FILE_TOO_BIG"
    assert await err(tg.upload_video("v.avi", JPEG, "video/avi")) == "FILE_TYPE_INVALID"
    w = await tg.upload_website_photo("w.png", PNG, "image/png")
    assert w["photo_id"].startswith("wp_")
    assert await err(tg.upload_website_photo("w.jpg", b"\xff\xd8" + b"0" * 2**20, "image/jpeg")) == "FILE_TOO_BIG"


async def test_an_uploaded_photo_belongs_to_its_account(tg):
    pid = (await tg.upload_photo("a.jpg", JPEG, "image/jpeg"))["photo_id"]
    other = TelegramAdsApi("mocktoken-b", base=TG_BASE)
    assert await err(other.edit_ad(101, photo_id=pid)) == "PHOTO_ID_INVALID"


# ================================================================ createAd


@pytest.mark.parametrize(
    ("missing", "code"),
    [
        ("title", "AD_TITLE_REQUIRED"),
        ("promote_url", "PROMOTE_URL_REQUIRED"),
        ("cpm", "CPM_REQUIRED"),
        ("target", "TARGET_REQUIRED"),
    ],
)
async def test_create_ad_required_parameters(tg, missing, code):
    assert await err(new_ad(tg, **{missing: None})) == code


async def test_create_ad_text_title_and_url_limits(tg):
    await new_ad(tg, text="x" * 160)
    assert await err(new_ad(tg, text="x" * 161)) == "AD_TEXT_TOO_LONG"
    assert await err(new_ad(tg, text=None)) == "AD_TEXT_REQUIRED"  # required except for search
    await new_ad(tg, text=None, placement="search_result", target={"type": "search", "search_queries": ["vpn"]})
    await new_ad(tg, title="ب" * 64)  # 128 bytes
    assert await err(new_ad(tg, title="ب" * 65)) == "AD_TITLE_TOO_LONG"  # 65 characters, 130 bytes
    await new_ad(tg, promote_url="https://t.me/" + "a" * 243)  # 256
    assert await err(new_ad(tg, promote_url="https://t.me/" + "a" * 244)) == "PROMOTE_URL_TOO_LONG"


def test_titles_are_cut_by_bytes_not_characters():
    assert len(clip_bytes("ب" * 100).encode()) == 128 and clip_bytes("ب" * 100) == "ب" * 64
    assert clip_bytes("abc") == "abc"
    assert len(clip_bytes("👇" * 40).encode()) <= 128  # never half a character


async def test_create_ad_placement(tg):
    assert (await new_ad(tg, placement=None))["placement"] == "channel_post"  # inferred from target.type
    bots = {"type": "bots", "bot_ids": ["@helper_bot"]}
    assert (await new_ad(tg, placement=None, target=bots))["placement"] == "bot_banner"
    assert await err(new_ad(tg, placement="banner")) == "PLACEMENT_INVALID"
    assert await err(new_ad(tg, placement="bot_banner")) == "PLACEMENT_INVALID"  # does not match channels


async def test_create_ad_photo_and_video(tg):
    pid = (await tg.upload_photo("a.jpg", JPEG, "image/jpeg"))["photo_id"]
    vid = (await tg.upload_video("v.mp4", MP4))["video_id"]
    ad = await new_ad(tg, photo_id=pid)
    assert ad["photo"]["photo_id"] == pid and "video" not in ad
    assert (await new_ad(tg, video_id=vid))["video"]["video_id"] == vid
    assert await err(new_ad(tg, photo_id=pid, video_id=vid)) == "MEDIA_INVALID"
    bots = {"type": "bots", "bot_ids": ["@helper_bot"]}
    assert await err(new_ad(tg, photo_id=pid, target=bots, placement="bot_banner")) == "MEDIA_NOT_ALLOWED"
    users = {"type": "users", "country_codes": ["IR"]}
    assert (await new_ad(tg, photo_id=pid, target=users))["photo"]["photo_id"] == pid
    assert await err(new_ad(tg, photo_id="ph_unknown")) == "PHOTO_ID_INVALID"


async def test_create_ad_budget_and_status(tg, tg_mock):
    free = await new_ad(tg, initial_budget=None)  # defaults to 0
    assert free["remaining_budget"] == 0 and free["status"] == "ready_for_review"
    paid = await new_ad(tg, initial_budget=2)
    assert paid["remaining_budget"] == 2 and paid["status"] == "in_review"
    assert await err(new_ad(tg, initial_budget=0.5)) == "BUDGET_TOO_LOW"
    tg_mock.set_balance("a", 1.5)
    assert await err(new_ad(tg, initial_budget=2)) == "NOT_ENOUGH_BUDGET"
    assert await err(new_ad(tg, cpm=0.01)) == "CPM_TOO_LOW"
    assert (await new_ad(tg, daily_budget_limit=0.2))["daily_budget_limit"] == 0.2


async def test_create_ad_returns_target_only_when_asked(tg):
    assert "target" in await new_ad(tg)
    assert "target" not in await new_ad(tg, return_target=None)


@pytest.mark.parametrize(("value", "ok"), [(0, False), (1, True), (4, True), (5, False)])
async def test_impression_frequency_1_to_4(tg, value, ok):
    if ok:
        assert (await new_ad(tg, impression_frequency=value))["impression_frequency"] == value
    else:
        assert await err(new_ad(tg, impression_frequency=value)) == "IMPRESSION_FREQUENCY_INVALID"


async def test_external_links_website_name_button_and_userpic(tg):
    site = "https://example.com/landing"
    assert await err(new_ad(tg, promote_url=site)) == "WEBSITE_NAME_REQUIRED"
    ad = await new_ad(tg, promote_url=site, website_name="Example", button="buy")
    assert ad["website_name"] == "Example" and ad["button"] == "buy"
    assert await err(new_ad(tg, promote_url=site, website_name="E" * 41)) == "WEBSITE_NAME_TOO_LONG"
    assert await err(new_ad(tg, promote_url=site, website_name="E", button="click")) == "BUTTON_INVALID"
    assert await err(new_ad(tg, button="buy")) == "BUTTON_INVALID"  # only for external links
    assert (await new_ad(tg, show_userpic=True))["show_userpic"] is True
    wp = (await tg.upload_website_photo("w.png", PNG, "image/png"))["photo_id"]
    assert (await new_ad(tg, promote_url=site, website_name="E", website_photo_id=wp))["website_photo"][
        "photo_id"
    ] == wp


async def test_activate_and_deactivate_dates(tg):
    now = int(time.time())
    later = await new_ad(tg, activate_date=now + 3600 + 17)
    assert later["is_paused"] is True and later["activate_date"] % 60 == 0  # rounded to the minute
    stop = await new_ad(tg, deactivate_date=now + 7200)
    assert stop["is_paused"] is False and stop["deactivate_date"] % 60 == 0
    assert await err(new_ad(tg, activate_date=now - 60)) == "ACTIVATE_DATE_INVALID"
    assert await err(new_ad(tg, deactivate_date=now + 366 * 86400)) == "DEACTIVATE_DATE_INVALID"
    assert (await new_ad(tg, is_paused=True))["is_paused"] is True
    assert (await new_ad(tg, is_paused=True, deactivate_date=now + 7200))["is_paused"] is False  # dates win


async def test_schedule(tg):
    week = [16777215, 0, 255, 0, 0, 0, 1]
    ad = await new_ad(tg, schedule={"week_hours_mask": week, "timezone": 12600})
    assert ad["schedule"] == {"week_hours_mask": week, "timezone": 12600}
    assert await err(new_ad(tg, schedule={"week_hours_mask": week[:6], "timezone": 0})) == "SCHEDULE_INVALID"
    assert (
        await err(new_ad(tg, schedule={"week_hours_mask": [16777216, *week[1:]], "timezone": 0})) == "SCHEDULE_INVALID"
    )
    assert await err(new_ad(tg, schedule={"week_hours_mask": week, "timezone": 1234})) == "SCHEDULE_TIMEZONE_INVALID"
    assert await err(new_ad(tg, schedule={"week_hours_mask": week})) == "SCHEDULE_TIMEZONE_INVALID"
    assert await err(new_ad(tg, schedule={"week_hours_mask": week, "use_viewer_timezone": True})) == "SCHEDULE_INVALID"
    users = {"type": "users", "country_codes": ["IR"]}
    viewer = await new_ad(tg, target=users, schedule={"week_hours_mask": week, "use_viewer_timezone": True})
    assert viewer["schedule"]["use_viewer_timezone"] is True


async def test_channel_targets(tg):
    assert await err(new_ad(tg, target={"type": "channels"})) == "TARGET_EMPTY"
    both = {"type": "channels", "channel_ids": ["@a_chan"], "language_codes": ["fa"]}
    assert await err(new_ad(tg, target=both)) == "TARGET_INVALID"
    topics = {"type": "channels", "topic_ids": [1, 2], "language_codes": ["fa"]}
    assert (await new_ad(tg, target=topics))["target"]["topics"][0]["topic_id"] == 1
    assert await err(new_ad(tg, target=topics | {"language_codes": ["fa", "en"]})) == "TARGET_INVALID"
    assert (
        await err(new_ad(tg, target={"type": "channels", "topic_ids": [99], "language_codes": ["fa"]}))
        == "TOPIC_ID_INVALID"
    )
    many = {"type": "channels", "channel_ids": [f"@chan_{i}" for i in range(101)]}
    assert await err(new_ad(tg, target=many)) == "TARGET_INVALID"
    langs = {"type": "channels", "language_codes": ["en", "fa", "ru", "a", "b", "c", "d", "e", "f"]}
    assert await err(new_ad(tg, target=langs)) == "TARGET_INVALID"
    assert await err(new_ad(tg, target="not json")) == "TARGET_INVALID"
    assert await err(new_ad(tg, target={"type": "planets"})) == "TARGET_INVALID"


async def test_numeric_channel_ids_need_a_resolution_first(tg):
    num = (await tg.target_channel("@daily_tech"))["channel_id"]
    other = TelegramAdsApi("mocktoken-b", base=TG_BASE)
    assert (
        await err(
            other.create_ad(
                "n1",
                title="t",
                text="t",
                promote_url="https://t.me/x",
                cpm=1,
                target={"type": "channels", "channel_ids": [num]},
            )
        )
        == "CHANNEL_ID_UNKNOWN"
    )
    ad = await new_ad(tg, target={"type": "channels", "channel_ids": [num]})
    assert ad["target"]["channels"][0]["username"] == "daily_tech"
    assert await err(new_ad(tg, target={"type": "channels", "channel_ids": [123456789]})) == "CHANNEL_ID_UNKNOWN"
    assert await err(tg.target_channel("123456789")) == "CHANNEL_ID_UNKNOWN"
    assert await err(tg.target_bot("987654321")) == "BOT_ID_UNKNOWN"
    assert await err(new_ad(tg, target={"type": "bots", "bot_ids": [987654321]}, placement="bot_banner")) == (
        "BOT_ID_UNKNOWN"
    )
    # A malformed username is still invalid, not unknown.
    assert await err(tg.target_channel("@a")) == "CHANNEL_INVALID"
    e = TelegramAdsError("createAd", "CHANNEL_ID_UNKNOWN")
    assert "@username" in e.friendly


def test_access_token_may_be_a_parameter(tg):
    """docs (Authorization): the token goes in the header or as the access_token parameter."""
    r = raw("getAccountInfo", token="", data={"access_token": "mocktoken-a"})
    assert r.json()["ok"] is True
    r = raw("getAccountInfo", token="", get=True, params={"access_token": "mocktoken-a"})
    assert r.json()["ok"] is True
    assert raw("getAccountInfo", token="", data={"access_token": "nope"}).json()["error"] == "ACCESS_TOKEN_INVALID"
    assert raw("getAccountInfo", token="").json()["error"] == "ACCESS_TOKEN_REQUIRED"


async def test_user_targets(tg):
    assert await err(new_ad(tg, target={"type": "users"})) == "COUNTRY_CODES_REQUIRED"
    ok = {"type": "users", "country_codes": ["IR"], "location_ids": [101, 102], "device": "android"}
    assert (await new_ad(tg, target=ok))["target"]["locations"][1]["name"] == "Mashhad"
    assert await err(new_ad(tg, target=ok | {"country_codes": ["IR", "DE"]})) == "LOCATION_ID_INVALID"
    assert await err(new_ad(tg, target=ok | {"location_ids": [201]})) == "LOCATION_ID_INVALID"
    assert await err(new_ad(tg, target=ok | {"device": "tv"})) == "DEVICE_INVALID"
    assert await err(new_ad(tg, target={"type": "users", "country_codes": ["XX"]})) == "COUNTRY_CODE_INVALID"
    pol = {"type": "users", "country_codes": [], "exclude_political_channels": True, "political_channels_only": True}
    assert await err(new_ad(tg, target=pol)) == "TARGET_INVALID"
    aud = [(await tg.create_audience(f"a{i}", key=f"au{i}"))["audience_id"] for i in range(5)]
    assert await err(new_ad(tg, target={"type": "users", "country_codes": [], "audience_ids": aud})) == "TARGET_INVALID"
    assert (
        await err(new_ad(tg, target={"type": "users", "country_codes": [], "audience_ids": [999]}))
        == "AUDIENCE_ID_INVALID"
    )


async def test_bot_and_search_targets(tg):
    assert await err(new_ad(tg, placement="bot_banner", target={"type": "bots", "bot_ids": []})) == "TARGET_EMPTY"
    assert (
        await err(new_ad(tg, placement="bot_banner", target={"type": "bots", "bot_ids": ["@bad_bot"]})) == "BOT_INVALID"
    )
    search = {"type": "search", "search_queries": [f"q{i}" for i in range(11)]}
    assert await err(new_ad(tg, placement="search_result", target=search)) == "SEARCH_QUERIES_INVALID"


# ================================================================ editAd


async def test_edit_ad_text_goes_to_review_when_the_ad_has_budget(tg, tg_mock):
    ad = await new_ad(tg, initial_budget=2)
    tg_mock.review("a", ad["ad_id"])
    out = await tg.edit_ad(ad["ad_id"], text="Brand new text")
    assert out["text"] == "Brand new text" and out["status"] == "in_review"


async def test_edit_ad_without_budget_stays_ready_for_review_until_submitted(tg):
    ad = await new_ad(tg, initial_budget=None)
    out = await tg.edit_ad(ad["ad_id"], text="Changed")
    assert out["status"] == "ready_for_review"
    assert (await tg.submit_for_review(ad["ad_id"]))["status"] == "in_review"
    assert await err(tg.submit_for_review(ad["ad_id"])) == "AD_STATUS_INVALID"  # only from ready_for_review


async def test_edits_that_do_not_need_review(tg, tg_mock):
    ad = await new_ad(tg, initial_budget=2)
    tg_mock.review("a", ad["ad_id"])
    out = await tg.edit_ad(ad["ad_id"], title="Internal name", cpm=0.7, impression_frequency=2, daily_budget_limit=0.3)
    assert out["status"] == "active" and out["title"] == "Internal name" and out["cpm"] == 0.7
    assert (await tg.set_paused(ad["ad_id"], True))["is_paused"] is True
    assert (await tg.set_paused(ad["ad_id"], False))["is_paused"] is False
    assert (await tg.edit_ad_status(ad["ad_id"], True))["is_paused"] is True  # deprecated, still works


async def test_edit_ad_validation(tg):
    ad = await new_ad(tg)
    assert await err(tg.call("editAd", text="x")) == "AD_ID_REQUIRED"
    assert await err(tg.edit_ad(999999, text="x")) == "AD_ID_INVALID"
    assert await err(tg.edit_ad(ad["ad_id"], text="x" * 161)) == "AD_TEXT_TOO_LONG"
    assert await err(tg.edit_ad(ad["ad_id"], title="ب" * 65)) == "AD_TITLE_TOO_LONG"
    assert await err(tg.edit_ad(ad["ad_id"], cpm=0.01)) == "CPM_TOO_LOW"
    assert await err(tg.edit_ad(ad["ad_id"], impression_frequency=5)) == "IMPRESSION_FREQUENCY_INVALID"


async def test_edit_ad_media_replaces_the_previous_one(tg):
    pid = (await tg.upload_photo("a.jpg", JPEG, "image/jpeg"))["photo_id"]
    vid = (await tg.upload_video("v.mp4", MP4))["video_id"]
    ad = await new_ad(tg, photo_id=pid)
    out = await tg.edit_ad(ad["ad_id"], video_id=vid)
    assert out["video"]["video_id"] == vid and "photo" not in out
    bot = await new_ad(tg, placement="bot_banner", target={"type": "bots", "bot_ids": ["@helper_bot"]})
    assert await err(tg.edit_ad(bot["ad_id"], photo_id=pid)) == "MEDIA_NOT_ALLOWED"


async def test_edit_ad_fields_that_cannot_change_once_set(tg):
    await tg.create_pixel(key="px")
    ev1 = (await tg.create_pixel_event("Buy", "purchase", key="e1"))["event_id"]
    ev2 = (await tg.create_pixel_event("Lead", "lead", key="e2"))["event_id"]
    site = {"promote_url": "https://example.com", "website_name": "Ex"}
    ad = await new_ad(tg, conversion_event_id=ev1, additional_info="Ltd", **site)
    assert await err(tg.edit_ad(ad["ad_id"], conversion_event_id=ev2)) == "CONVERSION_EVENT_ID_NOT_MODIFIABLE"
    assert await err(tg.edit_ad(ad["ad_id"], additional_info="Other")) == "ADDITIONAL_INFO_NOT_MODIFIABLE"
    assert await err(new_ad(tg, conversion_event_id=ev1)) == "CONVERSION_EVENT_ID_INVALID"  # telegram link


async def test_edit_ad_schedule_false_removes_it_and_dates_reset(tg):
    week = [1, 1, 1, 1, 1, 1, 1]
    ad = await new_ad(tg, schedule={"week_hours_mask": week, "timezone": 0}, activate_date=int(time.time()) + 600)
    out = await tg.edit_ad(ad["ad_id"], schedule=False, is_paused=False)
    assert "schedule" not in out and "activate_date" not in out and out["is_paused"] is False
    assert "target" in out  # the panel always asks for it


# ================================================================ budgets, review, delete


async def test_increase_and_decrease_ad_budget(tg, tg_mock):
    ad = await new_ad(tg, initial_budget=1)
    assert (await tg.topup(ad["ad_id"], 2, key="i1"))["remaining_budget"] == 3
    assert await err(tg.topup(ad["ad_id"], 0, key="i2")) == "AMOUNT_INVALID"
    tg_mock.inactive_seconds = 600
    tg_mock.review("a", ad["ad_id"])
    assert await err(tg.withdraw(ad["ad_id"], 1, key="d1")) == "AD_ACTIVE_RECENTLY"  # still active
    await tg.set_paused(ad["ad_id"], True)
    assert await err(tg.withdraw(ad["ad_id"], 1, key="d2")) == "AD_ACTIVE_RECENTLY"  # paused < 10 minutes
    tg_mock.accounts["a"]["paused_at"][ad["ad_id"]] -= 601
    assert (await tg.withdraw(ad["ad_id"], 1, key="d3"))["remaining_budget"] == 2
    assert await err(tg.withdraw(ad["ad_id"], 5, key="d4")) == "NOT_ENOUGH_BUDGET"
    txs = await tg.ad_transactions(ad["ad_id"])
    assert sorted(t["amount"] for t in txs["transactions"] if t["peer"]["type"] == "account") == [-1, 1, 2]


async def test_increasing_budget_does_not_submit_a_ready_ad(tg):
    ad = await new_ad(tg, initial_budget=None)
    assert (await tg.topup(ad["ad_id"], 1, key="t-ready"))["status"] == "ready_for_review"


async def test_delete_ad_only_after_10_minutes_inactive(tg, tg_mock):
    ad = await new_ad(tg, initial_budget=1)
    tg_mock.review("a", ad["ad_id"])
    assert await err(tg.delete_ad(ad["ad_id"])) == "AD_ACTIVE_RECENTLY"
    await tg.set_paused(ad["ad_id"], True)
    assert await tg.delete_ad(ad["ad_id"]) is True
    assert await tg.ads_by_id([ad["ad_id"]]) == {}
    assert await err(tg.delete_ad(ad["ad_id"])) == "AD_ID_INVALID"


# ================================================================ reading ads


async def test_get_ads_by_id(tg):
    got = await tg.call("getAdsById", ad_ids=[1, 999, 2])
    assert [a["ad_id"] for a in got] == [1, 2] and "target" not in got[0]
    assert await err(tg.call("getAdsById")) == "AD_IDS_REQUIRED"
    assert await err(tg.call("getAdsById", fmt="form", ad_ids="not a list")) == "AD_IDS_INVALID"


async def test_get_ads_list_pagination_and_limit(tg, tg_mock):
    first = await tg.ads_page(limit=5)
    assert len(first["ads"]) == 5 and first["total_count"] == len(tg_mock.accounts["a"]["ads"])
    seen = [a["ad_id"] for a in first["ads"]]
    nxt = first["next_offset"]
    while nxt:
        page = await tg.ads_page(limit=5, offset=nxt)
        seen += [a["ad_id"] for a in page["ads"]]
        nxt = page.get("next_offset")
    assert sorted(seen) == sorted(tg_mock.accounts["a"]["ads"])
    assert len((await tg.ads_page())["ads"]) == min(100, first["total_count"])  # defaults to 100
    assert (await tg.ads_page(limit=0))["ads"] == []
    assert await err(tg.ads_page(limit=101)) == "LIMIT_INVALID"
    assert all("target" in a for a in await tg.ads())  # the panel asks for targets


async def test_ad_fields_follow_the_ad_type(tg):
    ad = (await tg.ads_by_id([1]))[1]
    for k in ("ad_id", "title", "currency", "text", "promote_url", "cpm", "placement", "spent_budget",
              "remaining_budget", "views", "actions", "created_date", "status"):  # fmt: skip
        assert k in ad, k
    assert ad["status"] in ("stopped", "ready_for_review", "in_review", "declined", "active", "on_hold")


async def test_ad_transactions_and_stats(tg):
    txs = await tg.ad_transactions(1)
    assert txs["total_count"] and txs["transactions"][0]["peer"]["type"] in ("views", "account")
    views = [t for t in txs["transactions"] if t["peer"]["type"] == "views"]
    assert views and views[0]["amount"] <= 0 and "views" in views[0]["peer"]
    assert len((await tg.ad_transactions(1, limit=2))["transactions"]) == 2
    now = int(time.time())
    daily = await tg.ad_stats(1, now - 90 * 86400, now, 86400)
    assert daily and all(i["from_time"] % 86400 == 0 for i in daily)
    assert await err(tg.ad_stats(1, now - 1001 * 86400, now, 86400)) == "PERIOD_TOO_LONG"
    assert await err(tg.ad_stats(1, now, now - 86400 * 2, 86400)) == "TO_TIME_INVALID"
    assert await err(tg.ad_stats(1, now - 86400, now, 3600)) == "INTERVAL_INVALID"


# ================================================================ audiences


async def test_audiences(tg):
    phones = [f"+98912{i:07d}" for i in range(120)]
    small = await tg.create_audience("Buyers", key="aud1", add_phones=phones[:3])
    assert small["size"] == 50 and small["ads_count"] == 0  # "50 means less than 100"
    assert (await tg.create_audience("Empty", key="aud2"))["size"] == 0
    big = await tg.create_audience("Big", key="aud3", add_phones=phones)
    assert big["size"] == 120
    hashed = hashlib.sha256(b"989120000000").hexdigest()
    out = await tg.edit_audience(big["audience_id"], remove_phones=[hashed], add_phones=[hashed])
    assert out["size"] == 120  # deletions first, then additions
    out = await tg.edit_audience(big["audience_id"], reset_phones=True, remove_phones=phones[:1], add_phones=phones[:2])
    assert out["size"] == 50  # reset, remove ignored, then added
    assert await err(tg.create_audience("x" * 65, key="aud4")) == "AUDIENCE_TITLE_TOO_LONG"
    assert await err(tg.edit_audience(big["audience_id"], title="ب" * 33)) == "AUDIENCE_TITLE_TOO_LONG"  # 64 bytes
    assert await err(tg.create_audience("bad", key="aud5", add_phones=["12"])) == "PHONE_NUMBER_INVALID"
    assert await err(tg.create_audience("many", key="aud6", add_phones=phones * 84)) == "ADD_PHONES_TOO_MANY"
    lst = await tg.audiences()
    assert lst["total_count"] == 3 and len((await tg.audiences(limit=1))["audiences"]) == 1
    assert [a["title"] for a in await tg.audiences_by_id([big["audience_id"], 99])] == ["Big"]

    await new_ad(tg, target={"type": "users", "country_codes": ["IR"], "audience_ids": [small["audience_id"]]})
    assert (await tg.audiences_by_id([small["audience_id"]]))[0]["ads_count"] == 1
    assert await err(tg.delete_audience(small["audience_id"])) == "AUDIENCE_IN_USE"
    assert await tg.delete_audience(big["audience_id"]) is True
    assert await err(tg.delete_audience(big["audience_id"])) == "AUDIENCE_ID_INVALID"


# ================================================================ pixel


async def test_pixel_and_events(tg):
    assert await err(tg.pixel()) == "PIXEL_NOT_FOUND"
    px = await tg.create_pixel(key="p1")
    assert px["pixel_id"] and "<script" in px["code_snippet"]
    assert await tg.create_pixel(key="p2") == px  # one pixel per account
    assert await tg.pixel() == px
    events = await tg.pixel_events()
    auto = events["events"][0]
    assert auto["auto_created"] is True and events["total_count"] == 1
    assert "last_triggered_date" not in auto  # never triggered
    assert await err(tg.edit_pixel_event(auto["event_id"], "x")) == "EVENT_AUTO_CREATED"
    assert await err(tg.delete_pixel_event(auto["event_id"])) == "EVENT_AUTO_CREATED"

    ev = await tg.create_pixel_event("Bought", "purchase", key="e1")
    assert ev["status"] == "inactive" and ev["type"] == "purchase" and ev["ads_count"] == 0
    assert await err(tg.create_pixel_event("x", "landing_view", key="e2")) == "EVENT_TYPE_INVALID"
    assert await err(tg.create_pixel_event("x" * 65, "lead", key="e3")) == "EVENT_TITLE_TOO_LONG"
    assert (await tg.edit_pixel_event(ev["event_id"], "Paid"))["title"] == "Paid"
    assert await err(tg.call("editPixelEvent", event_id=ev["event_id"])) == "EVENT_TITLE_REQUIRED"
    assert await err(tg.edit_pixel_event(ev["event_id"], "ب" * 33)) == "EVENT_TITLE_TOO_LONG"
    assert [e["title"] for e in await tg.pixel_events_by_id([ev["event_id"], "nope"])] == ["Paid"]

    await new_ad(tg, promote_url="https://example.com", website_name="Ex", conversion_event_id=ev["event_id"])
    assert (await tg.pixel_events_by_id([ev["event_id"]]))[0]["ads_count"] == 1
    assert await err(tg.delete_pixel_event(ev["event_id"])) == "EVENT_IN_USE"
    spare = await tg.create_pixel_event("Spare", "lead", key="e4")
    assert await tg.delete_pixel_event(spare["event_id"]) is True


# ================================================================ targets


async def test_target_lists(tg):
    langs, topics, countries = await tg.target_languages(), await tg.target_topics(), await tg.target_countries()
    assert {"language_code", "name"} <= set(langs[0]) and {"topic_id", "name"} <= set(topics[0])
    assert {"country_code", "name"} <= set(countries[0])
    assert [loc["name"] for loc in await tg.target_locations_by_id([101, 201, 5])] == ["Tehran", "Berlin"]
    found = await tg.target_locations("IR", "ta")
    assert [loc["name"] for loc in found["locations"]] == ["Tabriz"] and found["total_count"] == 1
    page = await tg.target_locations("IR", "a", limit=2)  # query is required
    assert len(page["locations"]) == 2 and page["next_offset"] == "2"
    assert await err(tg.target_locations("ZZ", "a")) == "COUNTRY_CODE_INVALID"
    assert await err(tg.call("getTargetLocationsList", query="a")) == "COUNTRY_CODE_REQUIRED"


async def test_target_channel_and_bot(tg):
    ch = await tg.target_channel("@tech_news")
    assert set(ch) >= {"channel_id", "title", "username"} and ch["username"] == "tech_news"
    assert (await tg.call("getTargetChannel", channel_id=ch["channel_id"], for_excluding=True))[
        "username"
    ] == "tech_news"
    assert await err(tg.target_channel("@badchannel")) == "CHANNEL_INVALID"
    assert await err(tg.target_channel(str(10**9))) == "CHANNEL_ID_UNKNOWN"  # never resolved by this account
    bot = await tg.target_bot("@helper_bot")
    assert bot["bot_id"] and bot["username"] == "helper_bot"
    assert (await tg.target_bot(str(bot["bot_id"])))["username"] == "helper_bot"
    assert await err(tg.call("getTargetBot")) == "BOT_ID_REQUIRED"


def test_every_documented_method_is_in_the_client_and_the_fake_api():
    import inspect

    from app.integrations import telegram_ads
    from tests.mocks.tgads_api import METHODS, TgAdsApi

    assert len(METHODS) == 45  # "Available methods" of the documentation
    client = inspect.getsource(telegram_ads)
    for m in METHODS:
        assert f'"{m}"' in client, f"client has no call for {m}"
        assert hasattr(TgAdsApi, "m_" + m), f"fake API has no {m}"
