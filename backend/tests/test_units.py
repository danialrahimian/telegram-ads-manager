from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.integrations.bot_api import extract_rows, norm_transaction, norm_user, normalize_base_url, to_ts
from app.integrations.telegram_ads import TelegramAdsError, floor_amount


def test_extract_rows_accepts_common_shapes():
    rows = [{"id": 1}, {"id": 2}]
    assert extract_rows(rows) == rows
    assert extract_rows({"transactions": rows, "total": 2}) == rows
    assert extract_rows({"data": {"items": rows}}) == rows
    assert extract_rows({"whatever": rows}) == rows
    assert extract_rows({"count": 3}) == []
    assert extract_rows(None) == []


def test_to_ts_formats():
    tehran = ZoneInfo("Asia/Tehran")
    assert to_ts(1700000000) == 1700000000
    assert to_ts(1700000000123) == 1700000000
    assert to_ts("1700000000") == 1700000000
    utc = int(datetime(2026, 10, 2, 14, 21, tzinfo=UTC).timestamp())
    assert to_ts("2026-10-02 14:21:00") == utc
    assert to_ts("2026/10/02 14:21:00") == utc
    assert to_ts("2026-10-02 14:21:00", tehran) == utc - 3 * 3600 - 1800
    assert to_ts("2026-10-02") == int(datetime(2026, 10, 2, tzinfo=UTC).timestamp())
    assert to_ts("not a date") == 0
    assert to_ts(None) == 0


def test_normalizers():
    t = norm_transaction({"id": 5, "id_user": "123", "type": "buy", "amount": "59,000", "created_at": "2026-10-02"})
    assert t["ext_id"] == "5" and t["user_id"] == 123 and t["amount"] == 59000 and t["type"] == "buy"
    t2 = norm_transaction({"user_id": 9, "price": 10})
    t3 = norm_transaction({"user_id": 9, "price": 10})
    assert t2["ext_id"].startswith("h") and t2["ext_id"] == t3["ext_id"]  # stable hash when no id column
    u = norm_user({"chat_id": "777", "register": 1700000000})
    assert u == {"user_id": 777, "registered_ts": 1700000000}


def test_normalize_base_url():
    assert normalize_base_url("https://bot.example.com/api/docs") == "https://bot.example.com"
    assert normalize_base_url("bot.example.com/") == "https://bot.example.com"
    assert normalize_base_url("http://1.2.3.4:8080/panel/api") == "http://1.2.3.4:8080/panel"
    with pytest.raises(ValueError):
        normalize_base_url("ftp://x")


def test_floor_amount_never_rounds_up():
    assert floor_amount(1.234567) == 1.23456
    assert floor_amount(0.1 + 0.2) == 0.3


def test_friendly_errors():
    assert "توکن" in TelegramAdsError("getCurrentAccount", "ACCESS_TOKEN_INVALID").friendly
    assert "IP" in TelegramAdsError("getCurrentAccount", "IP_ADDRESS_NOT_ALLOWED").friendly


def test_crypto_and_passwords(env):
    from app.core import crypto
    from app.core.security import LoginLimiter, hash_password, verify_password

    enc = crypto.encrypt("secret-token-123")
    assert enc != "secret-token-123" and crypto.decrypt(enc) == "secret-token-123"
    assert crypto.last4("secret-token-123") == "-123"
    h = hash_password("hunter2hunter2")
    assert verify_password("hunter2hunter2", h) and not verify_password("nope", h)
    lim = LoginLimiter(max_failures=2, window=60)
    lim.fail("ip")
    assert not lim.blocked("ip")
    lim.fail("ip")
    assert lim.blocked("ip")


def test_new_credentials_reset_the_backoff():
    from app.models import Account
    from app.workers.scheduler import Scheduler

    acc = Account(slug="x", name="x", tg_token_enc="enc", settings={}, sync_state={})
    acc.sync_state = {"fast": {"last_try": 1000, "fails": 5, "error": "ACCESS_TOKEN_INVALID", "last_ok": 10}}
    assert Scheduler.due(acc, "fast", 1000 + 60) is False  # backing off after 5 failures
    from app.services.accounts import reset_sync

    reset_sync(acc, ("fast", "stats"))
    assert acc.sync_state["fast"]["fails"] == 0 and acc.sync_state["fast"]["last_ok"] == 10
    assert Scheduler.due(acc, "fast", 1000 + 60) is True


async def test_forced_trigger_reruns_after_the_running_sync(monkeypatch):
    import asyncio

    from app.services import sync
    from app.workers.scheduler import Scheduler

    calls: list[str] = []
    gate = asyncio.Event()

    async def fake_run(account_id: int, kind: str) -> None:
        calls.append(kind)
        if len(calls) == 1:
            await gate.wait()

    monkeypatch.setattr(sync, "run", fake_run)
    sch = Scheduler()
    sch._loop_task = asyncio.get_running_loop().create_future()  # pretend started without the loop
    assert sch.trigger(1, "fast") is True
    await asyncio.sleep(0)
    assert sch.trigger(1, "fast") is False  # already running, not forced: nothing queued
    assert sch.trigger(1, "fast", force=True) is False  # queued for right after
    gate.set()
    for _ in range(20):
        await asyncio.sleep(0)
    assert calls == ["fast", "fast"]
    sch._loop_task.cancel()


def test_user_growth_ignores_counter_resets():
    from app.services.metrics import user_growth

    assert user_growth([100, 101, 105, 105, 110]) == 10
    # a different bot was connected: 101202 -> 50, then normal growth
    assert user_growth([101150, 101202, 50, 101, 159]) == 52 + 51 + 58
    # and the other way round: a jump from a small to a big counter is not growth
    assert user_growth([50, 101202, 101210]) == 8
    assert user_growth([7]) == 0


# ---------------------------------------------------------------- v2.1: guidelines, budget history, rules


def test_guidelines_check():
    from app.services.guidelines import check

    assert check("سایکونت | تست رایگان VPN 👇")["issues"] == []
    bad = check("🔥🔥 فقط تا آخر امشب!!! تخفیف ویژه 🔥")
    codes = {i["code"] for i in bad["issues"]}
    assert bad["likely_violation"] and {"urgency", "punctuation", "emoji_repeat"} <= codes
    assert {i["code"] for i in check("خط اول\nخط دوم")["issues"]} == {"newline"}
    assert "too_long" in {i["code"] for i in check("x" * 161)["issues"]}
    links = check("ببینید https://bit.ly/abc و t.me/other_bot", promote_url="https://t.me/my_bot?start=1")
    assert {"links", "shortener", "link_mismatch"} <= {i["code"] for i in links["issues"]}
    # the ad's own link (with a start parameter) in the text is fine
    assert check("ربات ما: t.me/my_bot?start=9001", promote_url="https://t.me/my_bot?start=9001")["issues"] == []
    assert "language" in {i["code"] for i in check("Best VPN for you, try it")["issues"]}
    assert check("𝐒𝐔𝐏𝐄𝐑 سرعت")["likely_violation"]


def test_funded_days_from_budget_history():
    from datetime import date, timedelta

    from app.services.budget import funded_days

    today = date(2026, 10, 24)
    ts = lambda d: int(datetime.combine(d, datetime.min.time(), UTC).timestamp()) + 3600  # noqa: E731
    created = ts(today - timedelta(days=60))
    spend_days = [(today - timedelta(days=i)).isoformat() for i in range(20, 61)]
    # empty for 20 days, never seen funded: funded up to its last day with spend
    days = funded_days([], remaining=0, created_ts=created, spend_days=spend_days, today=today)
    assert max(days) == (today - timedelta(days=20)).isoformat() and len(days) == 41
    # topped up today after the gap: today plus the days before the gap; the 19 empty days are not funded
    days = funded_days(
        [(ts(today), "funded", 1.0)], remaining=1.0, created_ts=created, spend_days=spend_days, today=today
    )
    assert today.isoformat() in days and (today - timedelta(days=5)).isoformat() not in days
    assert (today - timedelta(days=20)).isoformat() in days
    # ran dry 3 days ago, funded before that
    days = funded_days(
        [(ts(today - timedelta(days=3)), "empty", 0.0)], remaining=0, created_ts=created, spend_days=[], today=today
    )
    assert (today - timedelta(days=3)).isoformat() in days and (today - timedelta(days=2)).isoformat() not in days


def test_budget_observe_records_transitions():
    from app.models import Ad
    from app.services import budget

    added: list = []

    class S:
        def add(self, x):
            added.append(x)

    ad = Ad(account_id=1, ad_id=5, remaining_budget=0.0, funded_since_ts=0, empty_since_ts=100)
    budget.observe(S(), ad, 1.0, 1000)  # topped up on the Telegram site
    assert ad.funded_since_ts == 1000 and ad.empty_since_ts == 0
    assert (added[-1].kind, added[-1].actor) == ("funded", "site")
    budget.observe(S(), ad, 0.9, 1100)  # normal spend: nothing recorded
    assert len(added) == 1
    budget.observe(S(), ad, 1.9, 1200, actor="app", kind="topup")
    assert (added[-1].kind, added[-1].actor) == ("topup", "app") and ad.funded_since_ts == 1000
    budget.observe(S(), ad, 0.0, 1300, actor="app", kind="withdraw")
    assert added[-1].kind == "withdraw" and ad.funded_since_ts == 0 and ad.empty_since_ts == 1300


def _row(**over):
    from app.services.accounts import DEFAULT_SETTINGS

    r = {
        "ad_id": 1,
        "status": "active",
        "running": True,
        "is_paused": False,
        "has_stats": True,
        "archived": False,
        "remaining": 1.0,
        "age_days": 60,
        "buyers": None,
        "cpp": None,
        "cps": 0.012,
        "spent": 3.0,
        "spent_7": 0.35,
        "cps_7": 0.012,
        "spent_f28": 1.4,
        "starts_f28": 120,
        "cps_f28": 0.0117,
        "funded_days_28": 28,
        "window_days": 28.0,
        "spent_window": 1.4,
        "empty_days": None,
        "zero_view_days": 0,
        "others_views_in_streak": 0,
        "decline_reason": "",
        "text": "سایکونت | تست رایگان 👇",
        "promote_url": "https://t.me/x_bot",
    }
    r.update(over)
    return r, dict(DEFAULT_SETTINGS)


def test_rules_judge_only_funded_days():
    from app.services.rules import evaluate

    # good ad, 20 days without budget, topped up today: funded window is a few hours -> no withdraw
    r, st = _row(remaining=3.0, window_days=0.2, spent_window=0.0)
    assert evaluate(r, st, None) is None
    r, st = _row(remaining=1.0, window_days=0.2, spent_window=0.0)
    assert evaluate(r, st, None)[0] == "topup"  # a winner with little budget left, never "stalled"
    # the same ad before the top-up: refill, with the number of days it has had no budget
    r, st = _row(remaining=0.0, running=True, empty_days=20, window_days=0)
    kind, amount, reason = evaluate(r, st, None)
    assert kind == "refill" and amount == 1.0 and "20 روز است بودجه ندارد" in reason
    # a really dead ad: 10 days with budget, nothing spent -> withdraw
    r, st = _row(remaining=2.0, window_days=10.0, spent_window=0.0, spent_f28=0.0, starts_f28=0, cps_f28=None)
    assert evaluate(r, st, None)[0] == "withdraw"
    # a paused (stopped on purpose) ad without budget is not refilled
    r, st = _row(remaining=0.0, is_paused=True, running=False, empty_days=3)
    assert evaluate(r, st, None) is None


def test_rules_replace():
    from app.services.rules import evaluate

    r, st = _row(status="declined", running=False, decline_reason="Misleading claims")
    kind, _, reason = evaluate(r, st, None)
    assert kind == "replace" and "Misleading claims" in reason
    r, st = _row(zero_view_days=6, others_views_in_streak=5000, spent_window=0.6)
    assert evaluate(r, st, None)[0] == "replace"
    r, st = _row(zero_view_days=6, others_views_in_streak=0, spent_window=0.6)  # the whole account is quiet
    assert evaluate(r, st, None) is None or evaluate(r, st, None)[0] != "replace"
    r, st = _row(text="فقط تا آخر امشب!!! 🔥🔥🔥")
    kind, _, reason = evaluate(r, st, None)
    assert kind == "replace" and "فوریت ساختگی" in reason


def test_referrer_assignment_groups_when_short():
    from app.services.referrals import assign

    rows = [{"ad_id": i, "target_label": f"@ch{i % 3}", "cpm": 0.4, "placement": "channel_post"} for i in range(1, 8)]
    pool = [{"referrer_id": str(9000 + i), "tag_id": None, "free": True, "check": None} for i in range(2)]
    out, warnings = assign(rows, pool, "target")
    assert len(out) == 2 and sorted(x for a in out for x in a["ad_ids"]) == list(range(1, 8))
    assert any("برچسب مشترک" in w for w in warnings)
    one_each, _ = assign(rows[:2], pool)
    assert [len(a["ad_ids"]) for a in one_each] == [1, 1]
    pool[0]["check"] = {"exists": True, "invited": 40}
    _, warnings = assign(rows[:1], pool)
    assert any("مخلوط" in w for w in warnings)
    assert assign(rows, [])[0] == []


def test_random_code_and_capability_probe_classification():
    from app.integrations.telegram_ads import TelegramAdsError, classify_probe
    from app.services.tracking import CODE_MIN, random_code

    codes = {random_code() for _ in range(200)}
    assert all(c.isdigit() and len(c) == 10 and int(c) >= CODE_MIN for c in codes) and len(codes) > 190
    assert classify_probe(TelegramAdsError("deleteAd", "METHOD_NOT_FOUND")) == "no"
    assert classify_probe(TelegramAdsError("deleteAd", "HTTP 404", 404)) == "no"
    assert classify_probe(TelegramAdsError("deleteAd", "AD_ID_REQUIRED")) == "yes"
    assert classify_probe(TelegramAdsError("deleteAd", "FLOOD_WAIT")) == "unknown"


def test_bot_diag_classification():
    from app.services.bot_diag import classify, users_jump

    assert classify({"error_kind": "dns"})["code"] == "dns"
    assert classify({"error_kind": "tls"})["code"] == "tls"
    assert classify({"error_kind": "timeout"})["code"] == "timeout"
    assert classify({"http_status": 404})["code"] == "not_found"
    assert classify({"http_status": 403, "msg": "token invalid"})["code"] == "auth"
    denied = classify({"http_status": 403, "msg": "access denied", "path": "/api/users"})
    assert denied["code"] == "access_denied" and "users" in denied["title"]
    assert classify({"http_status": 405})["code"] == "method"
    assert classify({"http_status": 429})["code"] == "rate_limited"
    err = classify({"http_status": 500, "excerpt": "Fatal error: boom"})
    assert err["code"] == "server_error" and "Fatal error: boom" in err["fix"]
    assert classify({"http_status": 200, "empty": True})["code"] == "empty"
    assert classify({"http_status": 200, "json": False, "excerpt": "hello"})["code"] == "not_json"
    assert (
        classify({"http_status": 200, "json": False, "content_type": "text/html", "redirected_to": "x/login"})["code"]
        == "redirect"
    )
    assert (
        classify({"http_status": 200, "json": True, "rows": 0, "obj_type": "list"}, expect_rows=True)["code"]
        == "ok_empty"
    )
    assert (
        classify({"http_status": 200, "json": True, "rows": 0, "obj_type": "str"}, expect_rows=True)["code"]
        == "unknown_shape"
    )
    assert (
        classify({"http_status": 200, "json": True, "envelope_ok": False, "msg": "Action Invalid", "action": "x"})[
            "code"
        ]
        == "bad_action"
    )
    assert (
        users_jump(101202, 50) and users_jump(1500, 100000) and not users_jump(1500, 1510) and not users_jump(None, 5)
    )


def test_scrub_removes_secrets():
    from app.integrations.bot_api import scrub

    out = scrub({"msg": "bad Token: botkey-xyz", "nested": ["key=botkey-xyz", "plain botkey-xyz"]}, ["botkey-xyz"])
    assert "botkey-xyz" not in str(out)
