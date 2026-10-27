"""In-memory fake of the Telegram Ads API with made-up data (no real accounts, no real money).

Two tokens are known: "mocktoken-a" (12 ads covering every rule) and "mocktoken-b" (4 ads).
The same object backs the pytest respx routes and the HTTP server used by the e2e tests.

The API itself (every documented method and rule) is in tgads_api.py. Switches for behaviour the panel must
cope with: `supports_delete` (default off: an API answering METHOD_NOT_FOUND for deleteAd, so the panel's
archive path stays covered), `supports_photo_edit`, `ignores_edits` / `ignored_formats` (editAd answers ok
but keeps the old content), `edits_wait_for_review` / `drops_pending`. `review()` plays the moderators.
"""

import random
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.mocks.tgads_api import MIN_INITIAL_BUDGET, TgAdsApi

TOKENS = {"mocktoken-a": "a", "mocktoken-b": "b"}
__all__ = ["MIN_INITIAL_BUDGET", "TOKENS", "TgAdsMock"]

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


class TgAdsMock(TgAdsApi):
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
        # Possible real behaviour: the edit waits for review and the API keeps returning the approved text
        # until moderators approve it; with drops_pending the approval keeps the old text anyway.
        self.edits_wait_for_review = False
        self.drops_pending = False
        # Request formats (json, form, query, multipart) whose editAd content Telegram ignores (edit probe tests).
        self.ignored_formats: set[str] = set()
        self.in_progress_keys: set[str] = set()
        # Text/link edits of an active, not paused ad are ignored (applied once the ad is paused).
        self.edits_need_pause = False  # idempotency keys "still running" (IDEMPOTENT_REQUEST_IN_PROGRESS)
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
                waiting = self.accounts[key].get("pending", {}).pop(a["ad_id"], {})
                if decision == "approve" and not self.drops_pending:
                    a.update(waiting)
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
