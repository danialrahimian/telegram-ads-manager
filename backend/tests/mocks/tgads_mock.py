"""In-memory fake of the Telegram Ads API with made-up data (no real accounts, no real money).

Two tokens are known: "mocktoken-a" (12 ads covering every rule) and "mocktoken-b" (4 ads).
The same object backs the pytest respx routes and the HTTP server used by the e2e tests.

What the real API does for deleting an ad or changing a live ad's photo is not public, so both are
switches: `supports_delete` (default off: unknown method, like an API without deleteAd) and
`supports_photo_edit` (default on). `review()` plays Telegram's moderators for ads in review.
"""

import json
import random
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

TOKENS = {"mocktoken-a": "a", "mocktoken-b": "b"}
# Telegram refuses createAd with an initial budget under 1 TON; the exact error code is not documented,
# so the mock answers BUDGET_TOO_LOW (the panel translates any budget-minimum error).
MIN_INITIAL_BUDGET = 1.0

# name, placement, cpm, days_running, starts_per_unit_early, starts_per_unit_late, daily_spend, quiet_days, remaining, start
SCENARIOS_A = [
    ("good_low_budget", "channel_post", 0.45, 60, 90, 90, 0.05, 0, 0.1, "ref_a1"),
    ("good_bot_low_budget", "bot_banner", 0.5, 60, 70, 70, 0.04, 0, 0.05, ""),
    ("good_plenty", "channel_post", 0.45, 50, 80, 80, 0.03, 0, 3.0, "ref_a2"),
    ("stalled_a", "channel_post", 0.45, 90, 120, 120, 0.06, 45, 2.4, ""),
    ("stalled_b", "bot_banner", 0.5, 90, 80, 80, 0.05, 40, 1.3, ""),
    ("expensive", "channel_post", 0.2, 60, 12, 12, 0.06, 0, 1.2, ""),
    ("slow_expensive", "channel_post", 0.2, 120, 8, 8, 0.012, 0, 0.8, ""),
    ("fatigued", "channel_post", 0.45, 45, 100, 30, 0.05, 0, 2.0, ""),
    ("new_in_review", "channel_post", 0.45, 0, 0, 0, 0, 0, 1.0, ""),
    ("old_stopped", "channel_post", 0.45, 120, 50, 50, 0.08, 60, 0.0, ""),
    # Good ad that had no budget for the last 20 days (spend stopped when the money ran out).
    ("refill_gap", "channel_post", 0.45, 60, 90, 90, 0.05, 20, 0.0, ""),
    # Declined by Telegram for its text.
    ("declined_text", "channel_post", 0.45, 0, 0, 0, 0, 0, 1.0, ""),
]
EXTRA = {
    "declined_text": {
        "status": "declined",
        "text": "🔥🔥 فقط تا آخر امشب!!! تخفیف ویژه 🔥",
        "decline_reason": {"text": "Misleading urgency and excessive emoji"},
    },
}
SCENARIOS_B = [
    ("darvaze_main", "channel_post", 0.4, 30, 60, 60, 0.05, 0, 1.5, "ref_b1"),
    ("darvaze_bot", "bot_banner", 0.5, 30, 40, 40, 0.04, 0, 0.8, ""),
    ("darvaze_expensive", "channel_post", 0.2, 40, 10, 10, 0.05, 0, 1.0, ""),
    ("darvaze_review", "channel_post", 0.4, 0, 0, 0, 0, 0, 1.0, ""),
]
BOT_USERNAME = {"a": "psychonet_test_bot", "b": "darvaze_test_bot"}


class TgAdsMock:
    def __init__(
        self,
        inactive_seconds: float = 3.0,
        seed: int = 7,
        *,
        supports_delete: bool = False,
        supports_photo_edit: bool = True,
        ignores_edits: bool = False,
    ) -> None:
        self.inactive_seconds = inactive_seconds
        self.supports_delete = supports_delete
        self.supports_photo_edit = supports_photo_edit
        # A Telegram that answers ok to editAd but keeps the old ad (the panel must notice).
        self.ignores_edits = ignores_edits
        self.lock = threading.Lock()
        self.accounts: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        rnd = random.Random(seed)
        for key, scen in (("a", SCENARIOS_A), ("b", SCENARIOS_B)):
            self.accounts[key] = self._build(key, scen, rnd)

    def _build(self, key: str, scenarios: list[tuple], rnd: random.Random) -> dict[str, Any]:
        today = datetime.now(UTC).date()
        ads: dict[int, dict[str, Any]] = {}
        stats: dict[int, list[dict[str, Any]]] = {}
        base_id = 1 if key == "a" else 101
        for i, (name, placement, cpm, days, early, late, spend, quiet, remaining, start) in enumerate(scenarios):
            ad_id = base_id + i
            created = today - timedelta(days=max(days, 1))
            rows, tot = [], {"views": 0, "clicks": 0, "actions": 0, "spent": 0.0}
            for d in range(days):
                day = created + timedelta(days=d)
                if day > today or (quiet and day > today - timedelta(days=quiet)):
                    continue
                sp = round(spend * rnd.uniform(0.6, 1.4), 5)
                rate = early if d < days / 2 else late
                starts = int(sp * rate * rnd.uniform(0.8, 1.2))
                views = int(sp / cpm * 1000)
                clicks = max(starts, int(views * 0.03))
                rows.append({"date": day.isoformat(), "views": views, "clicks": clicks, "actions": starts, "spent": sp})
                for k, v in (("views", views), ("clicks", clicks), ("actions", starts), ("spent", sp)):
                    tot[k] += v
            status = "in_review" if "review" in name else "stopped" if name == "old_stopped" else "active"
            extra = EXTRA.get(name, {})
            tkey = "bots" if placement == "bot_banner" else "channels"
            url = f"https://t.me/{BOT_USERNAME[key]}" + (f"?start={start}" if start else "")
            ads[ad_id] = {
                "ad_id": ad_id,
                "title": name,
                "text": "متن آزمایشی آگهی 👇",
                "promote_url": url,
                "cpm": cpm,
                "placement": placement,
                "status": status,
                "views": tot["views"],
                "clicks": tot["clicks"],
                "actions": tot["actions"],
                "action_type": "start_bot",
                "spent_budget": round(tot["spent"], 5),
                "remaining_budget": remaining,
                "currency": "TON",
                "created_date": int(datetime.combine(created, datetime.min.time(), UTC).timestamp()),
                "target": {"type": tkey, tkey: [{"username": f"sample_{name}", "title": name}]},
            }
            ads[ad_id].update({**extra, "status": extra.get("status", status)})
            stats[ad_id] = rows
        spent = round(sum(a["spent_budget"] for a in ads.values()), 5)
        return {
            "account": {
                "account_id": f"mock-{key}",
                "title": "Psychonet test" if key == "a" else "Darvaze test",
                "currency": "TON",
                "spent_budget": spent,
                "remaining_budget": 0.05,
                "ads_budget": round(sum(a["remaining_budget"] for a in ads.values()), 5),
            },
            "ads": ads,
            "stats": stats,
            "paused_at": {},
            "idem": {},
            "next_id": base_id + 900,
        }

    # ------------------------------------------------------------------ test helpers
    def review(self, key: str, ad_id: int | None = None, decision: str = "approve", reason: str = "") -> list[int]:
        """Approve or decline ads in review (one, or all of the account). Returns the ad ids changed."""
        with self.lock:
            done = []
            for a in self.accounts[key]["ads"].values():
                if a["status"] != "in_review" or (ad_id is not None and a["ad_id"] != ad_id):
                    continue
                if decision == "approve":
                    a["status"] = "active"
                    a.pop("decline_reason", None)
                else:
                    a["status"] = "declined"
                    a["decline_reason"] = {"text": reason or "Ad text violates the guidelines"}
                done.append(a["ad_id"])
            return done

    @staticmethod
    def _to_review(a: dict[str, Any]) -> None:
        """Real behaviour (Ads API, September 2026): an edited ad goes to review by itself only when it has
        budget; otherwise it waits as "ready_for_review" until submitAdForReview."""
        a["status"] = "in_review" if a["remaining_budget"] > 0 else "ready_for_review"
        a.pop("decline_reason", None)

    def set_ad(self, key: str, ad_id: int, **fields: Any) -> None:
        with self.lock:
            self.accounts[key]["ads"][ad_id].update(fields)

    def add_ads(self, key: str, n: int, placement: str = "channel_post", remaining: float = 1.0) -> list[int]:
        """Append `n` simple active ads (with a week of stats) for tests that need many ads."""
        with self.lock:
            st = self.accounts[key]
            today = datetime.now(UTC).date()
            out = []
            for _ in range(n):
                st["next_id"] += 1
                aid = st["next_id"]
                tkey = "bots" if placement == "bot_banner" else "channels"
                created = today - timedelta(days=7)
                st["ads"][aid] = {
                    "ad_id": aid,
                    "title": f"bulk_{aid}",
                    "text": "متن آزمایشی آگهی 👇",
                    "promote_url": f"https://t.me/{BOT_USERNAME[key]}",
                    "cpm": 0.4,
                    "placement": placement,
                    "status": "active",
                    "views": 7000,
                    "clicks": 200,
                    "actions": 70,
                    "action_type": "start_bot",
                    "spent_budget": 0.35,
                    "remaining_budget": remaining,
                    "currency": "TON",
                    "created_date": int(datetime.combine(created, datetime.min.time(), UTC).timestamp()),
                    "target": {"type": tkey, tkey: [{"username": f"sample_bulk_{aid}", "title": f"bulk {aid}"}]},
                }
                st["stats"][aid] = [
                    {
                        "date": (created + timedelta(days=d)).isoformat(),
                        "views": 1000,
                        "clicks": 28,
                        "actions": 10,
                        "spent": 0.05,
                    }
                    for d in range(7)
                ]
                out.append(aid)
            return out

    def set_balance(self, key: str, amount: float) -> None:
        with self.lock:
            self.accounts[key]["account"]["remaining_budget"] = amount

    # ------------------------------------------------------------------ dispatch
    def handle(
        self, token: str, method: str, params: dict[str, Any], files: dict[str, bytes], idem_key: str | None
    ) -> dict[str, Any]:
        key = TOKENS.get(token)
        if key is None:
            return {"ok": False, "error": "ACCESS_TOKEN_INVALID"}
        with self.lock:
            self.calls.append((key, method))
            st = self.accounts[key]
            if idem_key and idem_key in st["idem"]:
                return st["idem"][idem_key]
            try:
                res = {"ok": True, "result": self._method(st, method, params, files)}
            except KeyError as e:
                res = {"ok": False, "error": str(e.args[0])}
            if idem_key and res["ok"]:
                st["idem"][idem_key] = res
            return res

    def _method(self, st: dict[str, Any], method: str, p: dict[str, Any], files: dict[str, bytes]) -> Any:
        acct, ads = st["account"], st["ads"]
        if method == "getCurrentAccount":
            return acct
        if method == "getAdsList":
            items = sorted(ads.values(), key=lambda a: -a["ad_id"])
            off = int(p.get("offset") or 0)
            lim = int(p.get("limit") or 100)
            res: dict[str, Any] = {"total_count": len(items), "ads": items[off : off + lim]}
            if off + lim < len(items):
                res["next_offset"] = str(off + lim)
            return res
        if method == "getAdsById":
            ids = p["ad_ids"] if isinstance(p["ad_ids"], list) else json.loads(p["ad_ids"])
            return [ads[int(i)] for i in ids if int(i) in ads]
        if method == "getAdStats":
            aid = int(p["ad_id"])
            out = []
            for r in st["stats"].get(aid, []):
                ts = int(datetime.strptime(r["date"], "%Y-%m-%d").replace(tzinfo=UTC).timestamp())
                if int(p["from_time"]) <= ts < int(p["to_time"]):
                    out.append(
                        {
                            "from_time": ts,
                            "to_time": ts + 86400,
                            "views": r["views"],
                            "clicks": r["clicks"],
                            "actions": r["actions"],
                            "opens": 0,
                            "spent_budget": r["spent"],
                            "currency": "TON",
                        }
                    )
            return out
        if method == "editAd":
            if "photo_id" in p and self.supports_photo_edit and not str(p["photo_id"]).startswith("ph_"):
                raise KeyError("PHOTO_ID_INVALID")
            a = ads.get(int(p["ad_id"]))
            if not a:
                raise KeyError("AD_ID_INVALID")
            if self.ignores_edits:
                return a
            if "photo_id" in p and self.supports_photo_edit:
                a["photo"] = {"photo_id": p["photo_id"]}
                self._to_review(a)
            if "is_paused" in p:
                paused = p["is_paused"] in (True, "true", "True", 1, "1")
                a["is_paused"] = paused
                if paused:
                    st["paused_at"][a["ad_id"]] = time.time()
                else:
                    st["paused_at"].pop(a["ad_id"], None)
            for k in ("text", "promote_url"):
                if k in p:
                    if k == "text" and len(p[k]) > 160:
                        raise KeyError("AD_TEXT_TOO_LONG")
                    a[k] = p[k]
                    self._to_review(a)
            return a
        if method == "submitAdForReview":
            a = ads.get(int(p["ad_id"]))
            if not a:
                raise KeyError("AD_ID_INVALID")
            if a["status"] != "ready_for_review":
                raise KeyError("AD_STATUS_INVALID")
            a["status"] = "in_review"
            return a
        if method == "deleteAd" and self.supports_delete:
            if "ad_id" not in p:
                raise KeyError("AD_ID_REQUIRED")
            a = ads.get(int(p["ad_id"]))
            if not a:
                raise KeyError("AD_ID_INVALID")
            if a["remaining_budget"] > 0:
                raise KeyError("AD_BUDGET_NOT_EMPTY")
            del ads[a["ad_id"]]
            return True
        if method == "decreaseAdBudget":
            a = ads[int(p["ad_id"])]
            paused_at = st["paused_at"].get(a["ad_id"], time.time())
            if not a.get("is_paused") or time.time() - paused_at < self.inactive_seconds:
                raise KeyError("AD_ACTIVE_RECENTLY")
            amt = float(p["amount"])
            if amt > a["remaining_budget"] + 1e-9:
                raise KeyError("NOT_ENOUGH_BUDGET")
            a["remaining_budget"] = round(a["remaining_budget"] - amt, 5)
            acct["remaining_budget"] = round(acct["remaining_budget"] + amt, 5)
            acct["ads_budget"] = round(acct["ads_budget"] - amt, 5)
            return a
        if method == "increaseAdBudget":
            a = ads[int(p["ad_id"])]
            amt = float(p["amount"])
            if amt > acct["remaining_budget"] + 1e-9:
                raise KeyError("NOT_ENOUGH_BUDGET")
            a["remaining_budget"] = round(a["remaining_budget"] + amt, 5)
            acct["remaining_budget"] = round(acct["remaining_budget"] - amt, 5)
            acct["ads_budget"] = round(acct["ads_budget"] + amt, 5)
            return a
        if method in ("getTargetChannel", "getTargetBot"):
            u = str(p.get("channel_id") or p.get("bot_id"))
            if "bad" in u.lower():
                raise KeyError("CHANNEL_INVALID")
            return {"channel_id": 1, "username": u.lstrip("@"), "title": u}
        if method == "uploadAdPhoto":
            if "file" not in files:
                raise KeyError("FILE_REQUIRED")
            return {"photo_id": "ph_" + str(len(files["file"])), "photo_url": "https://example.invalid/p.jpg"}
        if method == "createAd":
            budget = float(p.get("initial_budget") or 0)
            if budget + 1e-9 < MIN_INITIAL_BUDGET:
                raise KeyError("BUDGET_TOO_LOW")
            if budget > acct["remaining_budget"] + 1e-9:
                raise KeyError("NOT_ENOUGH_BUDGET")
            if len(p["text"]) > 160:
                raise KeyError("AD_TEXT_TOO_LONG")
            st["next_id"] += 1
            aid = st["next_id"]
            t = p["target"] if isinstance(p["target"], dict) else json.loads(p["target"])
            names = t.get("channel_ids") or t.get("bot_ids") or []
            tkey = "channels" if t["type"] == "channels" else "bots"
            ad = {
                "ad_id": aid,
                "title": p["title"],
                "text": p["text"],
                "promote_url": p["promote_url"],
                "cpm": float(p["cpm"]),
                "placement": p["placement"],
                "status": "in_review",
                "views": 0,
                "clicks": 0,
                "actions": 0,
                "spent_budget": 0,
                "remaining_budget": budget,
                "created_date": int(time.time()),
                "currency": "TON",
                "target": {"type": t["type"], tkey: [{"username": n.lstrip("@"), "title": n} for n in names]},
            }
            if p.get("photo_id"):
                ad["photo"] = {"photo_id": p["photo_id"]}
            ads[aid] = ad
            acct["remaining_budget"] = round(acct["remaining_budget"] - budget, 5)
            acct["ads_budget"] = round(acct["ads_budget"] + budget, 5)
            return ad
        raise KeyError("METHOD_NOT_FOUND")
