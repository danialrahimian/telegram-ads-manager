"""v2.1: budget history, bulk operations (text/photo, referral links, replacements) and bot diagnostics."""

import contextlib
import json
import logging
import time

from sqlalchemy import select

from app.db.session import session_scope
from app.integrations.bot_api import BotApiError
from app.models import Account, Ad, AdBudgetEvent, BulkItem, BulkOperation, Job
from app.services import actions, bulk, sync
from tests.conftest import BOT_BASE, make_account

A = "/api/v1/accounts/psychonet"


async def _id(slug: str = "psychonet") -> int:
    async with session_scope() as s:
        return (await s.execute(select(Account.id).where(Account.slug == slug))).scalar_one()


async def _sync_all(slug: str = "psychonet") -> None:
    aid = await _id(slug)
    for kind in ("fast", "stats", "bot"):
        await sync.run(aid, kind)


async def _drain(rounds: int = 25) -> None:
    """Run queued jobs and bulk steps until nothing is left (waiting times skipped)."""
    aid = await _id()
    for _ in range(rounds):
        await actions.process_due()
        await bulk.advance(aid)
        async with session_scope() as s:
            left = (await s.execute(select(Job).where(Job.status.in_(("queued", "running"))))).scalars().all()
            for j in left:
                j.run_at = 0
            for op in (await s.execute(select(BulkOperation))).scalars():
                op.last_batch_ts = 0
        if not left:
            await bulk.advance(aid)
            async with session_scope() as s:
                if not (await s.execute(select(Job).where(Job.status == "queued"))).scalars().first():
                    return


async def _setup(api, tg_mock, balance: float = 10, **settings) -> None:
    tg_mock.set_balance("a", balance)
    await make_account(api)
    await api.patch(A, json={"settings": {"bulk_batch_interval_seconds": 0, **settings}})
    await _sync_all()


async def _items(op_id: int) -> list[BulkItem]:
    async with session_scope() as s:
        return list((await s.execute(select(BulkItem).where(BulkItem.op_id == op_id).order_by(BulkItem.id))).scalars())


# ---------------------------------------------------------------- 1) budget history


async def test_good_ad_topped_up_after_a_gap_is_not_stalled(api, tg_mock):
    await make_account(api)
    await _sync_all()
    rows = {a["title"]: a for a in (await api.get(f"{A}/ads")).json()["ads"]}
    gap = rows["refill_gap"]
    assert gap["empty_days"] == 20 and gap["remaining"] == 0
    pending = (await api.get(f"{A}/proposals")).json()["pending"]
    refill = next(p for p in pending if p["ad_id"] == gap["ad_id"])
    assert refill["kind"] == "refill" and "20 روز است بودجه ندارد" in refill["reason"]

    # The advertiser tops the ad up on the Telegram site: detected as a jump of remaining_budget.
    tg_mock.set_ad("a", gap["ad_id"], remaining_budget=1.0)
    await sync.run(await _id(), "fast")
    async with session_scope() as s:
        ev = (await s.execute(select(AdBudgetEvent).where(AdBudgetEvent.ad_id == gap["ad_id"]))).scalars().all()
        assert [(e.kind, e.actor) for e in ev] == [("funded", "site")]
        ad = (await s.execute(select(Ad).where(Ad.ad_id == gap["ad_id"]))).scalar_one()
        assert abs(ad.funded_since_ts - time.time()) < 30
    after = (await api.get(f"{A}/proposals")).json()
    kinds = {p["kind"] for p in after["pending"] if p["ad_id"] == gap["ad_id"]}
    assert "withdraw" not in kinds and "refill" not in kinds
    row = next(a for a in (await api.get(f"{A}/ads")).json()["ads"] if a["ad_id"] == gap["ad_id"])
    assert row["empty_days"] is None and row["window_days"] < 1
    # its cost per start is still measured on the funded days before the gap
    assert row["funded_days_28"] >= 28 and row["cps_f28"] is not None
    detail = (await api.get(f"{A}/ads/{gap['ad_id']}")).json()
    assert detail["budget_history"][0]["kind"] == "funded"

    # Ten days later it still has not spent anything: now it really is stalled.
    async with session_scope() as s:
        ad = (await s.execute(select(Ad).where(Ad.ad_id == gap["ad_id"]))).scalar_one()
        ad.funded_since_ts -= 10 * 86400
        for e in (await s.execute(select(AdBudgetEvent).where(AdBudgetEvent.ad_id == gap["ad_id"]))).scalars():
            e.ts -= 10 * 86400
    await api.post(f"{A}/proposals/run")
    pending = (await api.get(f"{A}/proposals")).json()["pending"]
    assert any(p["ad_id"] == gap["ad_id"] and p["kind"] == "withdraw" for p in pending)


async def test_refill_approval_and_replace_needs_the_dialog(api, tg_mock):
    await make_account(api)
    await _sync_all()
    tg_mock.set_balance("a", 5)
    r = await api.post(f"{A}/proposals/approve-kind", json={"kind": "refill"})
    assert r.status_code == 200 and r.json()["approved"] == 1
    replace = next(p for p in (await api.get(f"{A}/proposals")).json()["pending"] if p["kind"] == "replace")
    r = await api.post(f"{A}/proposals/{replace['id']}/approve")
    assert r.status_code == 400 and "جایگزین" in r.json()["detail"]
    await _drain()
    assert tg_mock.accounts["a"]["ads"][11]["remaining_budget"] == 1.0


async def test_migration_counts_existing_ads_as_funded_from_now(tmp_path, monkeypatch):
    import sqlite3

    from alembic import command

    from app.core.config import get_settings
    from app.db.migrate import alembic_config

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        cfg = alembic_config()
        command.upgrade(cfg, "0001")
        db = sqlite3.connect(tmp_path / "app.db")
        db.execute(
            "insert into accounts (id,slug,name,color,created_at,tg_token_last4,tg_account_id,tg_title,currency,"
            "spent_budget,remaining_budget,ads_budget,tg_status,tg_error,tg_checked_ts,bot_base_url,bot_username,"
            "bot_key_last4,bot_status,bot_error,bot_checked_ts,bot_stats,bot_fields,last_tx_ts,settings,sync_state,"
            "automation_paused) values (1,'a','a','teal','2026-01-01','','','','',0,0,0,'','',0,'','','','','',0,"
            "'{}','{}',0,'{}','{}',0)"
        )
        for ad_id, remaining in ((5, 2.0), (6, 0.0)):
            db.execute(
                "insert into ads (account_id,ad_id,title,text,promote_url,start_code,cpm,placement,status,is_paused,"
                "views,clicks,actions,action_type,spent_budget,remaining_budget,daily_budget_limit,created_date,"
                "target_type,target_label,target,has_photo,has_video,decline_reason,gone,synced_ts,stats_ts,"
                "stats_spent,note,created_by_app) values (1,?,'','','','',0,'','active',0,0,0,0,'',0,?,0,1000,'',"
                "'','{}',0,0,'',0,0,0,-1,'',0)",
                (ad_id, remaining),
            )
        db.commit()
        command.upgrade(cfg, "head")
        rows = dict(db.execute("select ad_id, funded_since_ts from ads").fetchall())
        assert abs(rows[5] - time.time()) < 60 and rows[6] == 0
        db.close()
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------- 2) bulk operations


async def test_bulk_text_replace_flow_archives_old_ads(api, tg_mock):
    await _setup(api, tg_mock)
    new_text = "سایکونت | اینترنت پایدار با تست رایگان 👇"
    body = {"kind": "content", "ad_ids": [3, 8], "mode": "replace", "text": new_text, "budget_source": "transfer"}

    # The guideline check blocks a bad text before anything happens.
    bad = (await api.post(f"{A}/bulk/preview", json={**body, "text": "فقط تا آخر امشب!!! 🔥🔥🔥"})).json()
    assert bad["errors"] and all(i["blocked"] for i in bad["items"])
    # Not enough balance for the initial budgets when they come from the account.
    tg_mock.set_balance("a", 0.05)
    poor = (await api.post(f"{A}/bulk/preview", json={**body, "budget_source": "account", "initial_budget": 1})).json()
    assert any("موجودی حساب" in e for e in poor["errors"])
    tg_mock.set_balance("a", 10)

    p = (await api.post(f"{A}/bulk/preview", json=body)).json()
    assert not p["errors"] and p["retire"] == "archive" and p["totals"]["ads"] == 2
    it = p["items"][0]
    assert any(c["field"] == "text" and c["new"] == new_text for c in it["changes"])
    assert it["money"]["from_account"] == 1.0 and it["money"]["from_old"] > 0  # Telegram's minimum, 1 TON
    assert any("بررسی تلگرام" in w for w in it["warnings"]) and any("بایگانی" in w for w in it["warnings"])
    assert (await api.post(f"{A}/bulk", json=body)).status_code == 400  # no explicit confirmation

    r = await api.post(f"{A}/bulk", json={**body, "confirm": True})
    assert r.status_code == 201, r.text
    op_id = r.json()["id"]
    await _drain()
    items = await _items(op_id)
    assert all(i.status == "in_review" and i.new_ad_id for i in items)
    for i in items:  # nothing happened to the old ads yet
        old = tg_mock.accounts["a"]["ads"][i.ad_id]
        assert not old.get("is_paused") and old["remaining_budget"] > 0
        new = tg_mock.accounts["a"]["ads"][i.new_ad_id]
        assert new["text"] == new_text and new["cpm"] == old["cpm"] and new["remaining_budget"] == 1.0

    tg_mock.review("a", decision="approve")
    await sync.run(await _id(), "fast")
    await _drain()
    items = await _items(op_id)
    assert [i.status for i in items] == ["retired", "retired"], [(i.status, i.step, i.error) for i in items]
    for i in items:
        old = tg_mock.accounts["a"]["ads"][i.ad_id]
        assert old["is_paused"] and old["remaining_budget"] == 0
        new = tg_mock.accounts["a"]["ads"][i.new_ad_id]
        assert new["remaining_budget"] > 1.0  # the excess of the old budget was moved (up to one top-up)
        assert i.withdrawn > 0
    ads = {a["ad_id"]: a for a in (await api.get(f"{A}/ads")).json()["ads"]}
    assert 3 not in ads and 8 not in ads  # archived ads are hidden by default
    archived = (await api.get(f"{A}/ads?archived=true")).json()
    assert {a["ad_id"] for a in archived["ads"]} == {3, 8}
    new_ads = [ads[i.new_ad_id] for i in items]
    assert all(a["variant_id"] and a["text"] == new_text for a in new_ads)
    texts = (await api.get(f"{A}/texts")).json()["texts"]
    assert any(t["text"] == new_text and t["performance"]["ads"] == 2 for t in texts)
    op = (await api.get(f"{A}/bulk/{op_id}")).json()
    assert op["status"] == "done" and op["progress"] == 1.0


async def test_bulk_declined_new_ad_leaves_old_untouched_and_can_be_resubmitted(api, tg_mock):
    await _setup(api, tg_mock)
    body = {"kind": "content", "ad_ids": [3], "mode": "replace", "text": "سایکونت | VPN سریع 👇", "confirm": True}
    op_id = (await api.post(f"{A}/bulk", json=body)).json()["id"]
    await _drain()
    item = (await _items(op_id))[0]
    tg_mock.review("a", item.new_ad_id, decision="decline", reason="Text is not clear")
    await sync.run(await _id(), "fast")
    await _drain()
    item = (await _items(op_id))[0]
    assert item.status == "declined" and item.decline_reason == "Text is not clear"
    old = tg_mock.accounts["a"]["ads"][3]
    assert not old.get("is_paused") and old["remaining_budget"] == 3.0
    detail = (await api.get(f"{A}/bulk/{op_id}")).json()
    assert detail["items"][0]["decline_reason"] == "Text is not clear"

    r = await api.post(f"{A}/bulk/{op_id}/items/{item.id}/resubmit", json={"text": "تا آخر امشب!!!"})
    assert r.status_code == 400
    r = await api.post(f"{A}/bulk/{op_id}/items/{item.id}/resubmit", json={"text": "سایکونت | اتصال پایدار 👇"})
    assert r.status_code == 200
    await _drain()
    assert tg_mock.accounts["a"]["ads"][item.new_ad_id]["text"] == "سایکونت | اتصال پایدار 👇"
    tg_mock.review("a", item.new_ad_id)
    await sync.run(await _id(), "fast")
    await _drain()
    assert (await _items(op_id))[0].status == "retired"


async def test_bulk_photo_in_place_needs_a_known_capability(api, tg_mock):
    await _setup(api, tg_mock)
    r = await api.post(f"{A}/bulk/photo", files={"photo": ("p.png", b"\x89PNG-fake", "image/png")})
    assert r.status_code == 200
    photo_id = r.json()["photo_id"]
    r = await api.post(f"{A}/bulk/photo", files={"photo": ("p.gif", b"GIF", "image/gif")})
    assert r.status_code == 400
    body = {"kind": "content", "ad_ids": [3, 8], "mode": "inplace", "photo_id": photo_id}
    p = (await api.post(f"{A}/bulk/preview", json=body)).json()
    assert any("آزمایش نشده" in e for e in p["errors"])
    cap = (await api.post(f"{A}/bulk/capabilities/probe")).json()
    assert cap["edit_photo"] == "yes" and cap["delete_ad"] == "no"
    p = (await api.post(f"{A}/bulk/preview", json=body)).json()
    assert not p["errors"] and "آمارش حفظ می‌شود" in p["items"][0]["warnings"][0]
    op_id = (await api.post(f"{A}/bulk", json={**body, "confirm": True})).json()["id"]
    await _drain()
    for ad_id in (3, 8):
        raw = tg_mock.accounts["a"]["ads"][ad_id]
        assert raw["photo"]["photo_id"] == photo_id and raw["status"] == "in_review"
    tg_mock.review("a")
    await sync.run(await _id(), "fast")
    await _drain()
    assert [i.status for i in await _items(op_id)] == ["approved", "approved"]
    ads = {a["ad_id"]: a for a in (await api.get(f"{A}/ads")).json()["ads"]}
    assert ads[3]["spent"] > 0 and ads[3]["has_photo"]  # same ad: statistics kept


async def test_bulk_photo_capability_unknown_one_ad_test(api, tg_mock):
    tg_mock.supports_photo_edit = False  # the API ignores photo_id on editAd
    await _setup(api, tg_mock)
    photo_id = (await api.post(f"{A}/bulk/photo", files={"photo": ("p.jpg", b"\xff\xd8x", "image/jpeg")})).json()[
        "photo_id"
    ]
    assert (await api.post(f"{A}/bulk/capabilities/probe")).json()["edit_photo"] == "unknown"
    body = {"kind": "content", "ad_ids": [3], "mode": "inplace", "photo_id": photo_id}
    p = (await api.post(f"{A}/bulk/preview", json=body)).json()
    assert p["photo_test"] and not p["errors"]
    op_id = (await api.post(f"{A}/bulk", json={**body, "confirm": True})).json()["id"]
    await _drain()
    item = (await _items(op_id))[0]
    assert "تأیید نکرد" in item.error
    cap = (await api.get(f"{A}/bulk/capabilities")).json()
    assert cap["edit_photo"] == "unknown"


async def test_bulk_replace_deletes_old_ads_when_the_api_can(api, tg_mock):
    tg_mock.supports_delete = True
    await _setup(api, tg_mock)
    assert (await api.post(f"{A}/bulk/capabilities/probe")).json()["delete_ad"] == "yes"
    same = (await api.post(f"{A}/bulk/preview", json={"kind": "replace", "ad_ids": [12], "photo_id": "ph_1"})).json()
    assert "همان متن ساخته نمی‌شود" in same["items"][0]["blocked"]  # the declined text is not copied
    body = {"kind": "replace", "ad_ids": [12], "per_ad": {"12": {"text": "سایکونت | تست رایگان VPN 👇"}}}
    p = (await api.post(f"{A}/bulk/preview", json=body)).json()
    assert p["retire"] == "delete" and not p["errors"]
    proposal = next(x for x in (await api.get(f"{A}/proposals")).json()["pending"] if x["kind"] == "replace")
    op = (await api.post(f"{A}/bulk", json={**body, "confirm": True, "proposal_ids": [proposal["id"]]})).json()
    assert proposal["id"] not in [x["id"] for x in (await api.get(f"{A}/proposals")).json()["pending"]]
    await _drain()
    tg_mock.review("a")
    await sync.run(await _id(), "fast")
    await _drain()
    item = (await _items(op["id"]))[0]
    assert item.status == "retired", (item.step, item.error)
    assert 12 not in tg_mock.accounts["a"]["ads"]  # deleted on Telegram


async def test_bulk_batches_and_stop(api, tg_mock):
    await _setup(api, tg_mock, bulk_batch_size=1, bulk_batch_interval_seconds=3600)
    body = {"kind": "content", "ad_ids": [1, 3, 8], "mode": "inplace", "text": "سایکونت | VPN سریع 👇", "confirm": True}
    op_id = (await api.post(f"{A}/bulk", json=body)).json()["id"]
    await actions.process_due()
    await bulk.advance(await _id())
    assert [i.status for i in await _items(op_id)] == ["in_review", "pending", "pending"]  # one per batch
    r = await api.post(f"{A}/bulk/{op_id}/stop")
    assert r.status_code == 200
    items = await _items(op_id)
    assert [i.status for i in items] == ["in_review", "skipped", "skipped"]
    assert (await api.get(f"{A}/bulk/{op_id}")).json()["status"] == "stopped"
    assert (await api.post(f"{A}/bulk/{op_id}/stop")).status_code == 400
    # an ad inside a running operation cannot join another one
    op2 = (await api.post(f"{A}/bulk", json={**body, "ad_ids": [6]})).json()["id"]
    p = (await api.post(f"{A}/bulk/preview", json={**body, "ad_ids": [6, 7]})).json()
    assert "عملیات گروهی دیگر" in p["items"][0]["blocked"] and op2


async def test_bulk_referral_links_from_the_pool(api, tg_mock, bot_mock):
    await _setup(api, tg_mock)
    r = await api.patch(A, json={"settings": {"referrer_pool": ["9001", "9500", "abc"]}})
    assert r.status_code == 400
    await api.patch(A, json={"settings": {"referrer_pool": ["9001", "9500", "9600"]}})
    tr = (await api.post(f"{A}/tracking/pool/check")).json()["pool"]
    by_id = {p["referrer_id"]: p for p in tr}
    assert by_id["9001"]["check"]["invited"] == 40 and by_id["9500"]["free"]
    info = (await api.get(f"{A}/tracking")).json()
    assert [m["key"] for m in info["modes"]] == ["pool", "shared", "random"]
    assert len(info["random_example"]) == 10 and "start_code" in info["campaign_request"]
    assert info["random"]["result"] == "unknown"
    assert (await api.patch(A, json={"settings": {"random_tracking": "works"}})).status_code == 400
    sales = (await api.get(f"{A}/sales")).json()["overview"]
    untagged_before = sales["untagged_ads"]
    assert untagged_before > 0

    ads = [4, 5, 6, 7, 8]
    body = {"kind": "referral", "ad_ids": ads, "mode": "inplace", "group_by": "target"}
    p = (await api.post(f"{A}/bulk/preview", json=body)).json()
    assert not p["errors"] and len(p["assignments"]) == 3
    assert any("برچسب مشترک" in w for w in p["warnings"]) and any("مخلوط" in w for w in p["warnings"])
    op_id = (await api.post(f"{A}/bulk", json={**body, "confirm": True})).json()["id"]
    await _drain()
    urls = {tg_mock.accounts["a"]["ads"][i]["promote_url"] for i in ads}
    assert urls == {f"https://t.me/psychonet_test_bot?start={r}" for r in ("9001", "9500", "9600")}
    tg_mock.review("a")
    await _sync_all()
    await _drain()
    assert all(i.status == "approved" for i in await _items(op_id))
    tags = {t["referrer_id"]: t for t in (await api.get(f"{A}/tags")).json()["tags"]}
    assert set(tags) == {"9001", "9500", "9600"} and all(t["start_code"] == t["referrer_id"] for t in tags.values())
    rows = {a["ad_id"]: a for a in (await api.get(f"{A}/ads")).json()["ads"]}
    assert all(rows[i]["tag_id"] for i in ads)
    assert (await api.get(f"{A}/sales")).json()["overview"]["untagged_ads"] == untagged_before - len(ads)
    assert tags["9001"]["users"] == 40  # referral users are read for the new tags
    # every referrer now has ads: nothing is free any more
    p = (await api.post(f"{A}/bulk/preview", json={**body, "ad_ids": [1, 2]})).json()
    assert p["errors"]


async def test_text_check_and_claude_fix(api):
    await make_account(api)
    await _sync_all()
    r = (await api.post(f"{A}/texts/check", json={"text": "فقط تا آخر امشب!!! 🔥🔥🔥"})).json()
    assert r["likely_violation"] and any(i["code"] == "urgency" for i in r["issues"])
    assert (await api.post(f"{A}/texts/fix", json={"text": "x"})).json()["code"] == "claude_error"  # no key yet
    await api.put("/api/v1/settings", json={"claude_api_key": "sk-ant-test"})
    fix = (await api.post(f"{A}/texts/fix", json={"text": "فقط تا آخر امشب!!! 🔥🔥🔥", "ad_id": 12})).json()
    assert fix["text"].startswith("سایکونت") and not fix["check"]["likely_violation"] and fix["before"]["issues"]


# ---------------------------------------------------------------- 3) bot diagnostics


async def test_bot_diagnose_without_permission(api):
    await make_account(api, bot_key="botkey-limited")
    await api.patch(A, json={"settings": {"referrer_pool": ["9001"]}})
    r = await api.post(f"{A}/bot/diagnose")
    assert r.status_code == 200
    rep = r.json()["report"]
    rows = {x["name"]: x for x in rep["rows"]}
    assert rows["statbot"]["ok"] and rows["statbot"]["http_status"] == 200
    tx = rows["transactions:plain"]
    assert not tx["ok"] and tx["cause"]["code"] == "access_denied" and "users" in tx["cause"]["title"]
    assert tx["method"] == "GET" and tx["path"] == "/api/users" and tx["action"] == "transactions"
    assert tx["body"] == {"page": 1, "limit": 5, "actions": "transactions"} and tx["msg"] == "access denied"
    assert rows["affiliates:9001"]["cause"]["code"] == "access_denied"
    assert rows["settings"]["ok"] and rep["identity"]["reported_username"] == "psychonet_test_bot"
    assert rows["invoices"]["ok"]  # tried because every ledger request failed
    text = r.json()["text"]
    assert "GET /api/users  actions=transactions" in text and "HTTP 403" in text and "botkey-limited" not in text
    again = (await api.get(f"{A}/bot/diagnose")).json()
    assert again["report"]["ts"] == rep["ts"]


async def test_bot_diagnose_server_error_and_sync_parts(api):
    await make_account(api, bot_key="botkey-500")
    rep = (await api.post(f"{A}/bot/diagnose")).json()["report"]
    tx = next(x for x in rep["rows"] if x["name"] == "transactions:plain")
    assert tx["cause"]["code"] == "server_error" and "Fatal error" in tx["excerpt"] and tx["http_status"] == 500
    assert "Fatal error" in tx["cause"]["fix"]
    with contextlib.suppress(BotApiError):  # the bot sync reports the broken ledger as an error
        await sync.run(await _id(), "bot")
    acc = (await api.get(A)).json()
    parts = acc["sync"]["bot"]["parts"]
    assert parts["stats"]["ok"] and parts["transactions"]["code"] == "server_error"


async def test_bot_sync_zero_transactions_is_its_own_state(api, bot_mock):
    await make_account(api)
    bot_mock.data["a"]["txs"] = []
    await sync.run(await _id(), "bot")
    parts = (await api.get(A)).json()["sync"]["bot"]["parts"]
    tx = parts["transactions"]
    assert tx["ok"] and tx["code"] == "ok_empty" and tx["rows"] == 0
    assert tx["filters"]["purchase_types"] == ["buy", "buy_extend"] and tx["filters"]["from"] > 0


async def test_bot_key_never_leaks(api, caplog):
    caplog.set_level(logging.DEBUG)
    await make_account(api, bot_key="botkey-echo")
    r = await api.post(f"{A}/bot/diagnose")
    assert "botkey-echo" not in r.text
    assert "Fatal error" in r.text  # the rest of the page is still shown
    with contextlib.suppress(BotApiError):  # the bot sync reports the broken ledger as an error
        await sync.run(await _id(), "bot")
    for path in (A, f"{A}/bot/diagnose", f"{A}/activity", f"{A}/live"):
        assert "botkey-echo" not in (await api.get(path)).text
    assert "botkey-echo" not in caplog.text
    async with session_scope() as s:
        acc = (await s.execute(select(Account))).scalar_one()
        assert "botkey-echo" not in json.dumps(acc.sync_state) + json.dumps(acc.bot_fields) + acc.bot_error


async def test_bot_identity_and_user_count_warnings(api):
    await make_account(api, bot_key="botkey-otherbot")  # bot B's key in account A
    async with session_scope() as s:
        acc = (await s.execute(select(Account))).scalar_one()
        acc.sync_state = {**acc.sync_state, "bot_users_last": 101202}
    rep = (await api.post(f"{A}/bot/diagnose")).json()["report"]
    joined = " ".join(rep["warnings"])
    assert "darvaze_test_bot" in joined and "ربات یا کلید دیگری" in joined
    assert rep["identity"]["count_user"] == 600
    r = await api.put(f"{A}/bot", json={"base_url": BOT_BASE, "username": "psychonet_test_bot", "api_key": "botkey-a"})
    assert r.status_code == 200
