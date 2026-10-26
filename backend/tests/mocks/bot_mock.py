"""In-memory fake of the sales bot admin API (Token header, `actions` dispatch, {status,msg,obj}).

Two keys: "botkey-a" (bot of account A) and "botkey-b". "botkey-limited" is a key without the
`users` permission, to exercise the 403 path. Referrers 9001/9002 (A) and 9101 (B) each referred users,
some of whom bought.
"""

import random
import threading
from datetime import datetime, timedelta
from typing import Any

KEYS = {
    "botkey-a": "a",
    "botkey-b": "b",
    "botkey-limited": "a",
    "botkey-crash": "b",
    "botkey-500": "a",
    "botkey-echo": "a",
    "botkey-otherbot": "b",
}
# "botkey-500": statbot works, every /api/users action dies with an HTTP 500 PHP error page.
# "botkey-echo": like botkey-500, but the error page repeats the Token header (the key must never leak).
# "botkey-otherbot": the key of bot B used in account A (setting_info reports another username).
BOT_NAMES = {"a": "psychonet_test_bot", "b": "darvaze_test_bot"}
# Referrer accounts the advertiser owns but which have not invited anyone yet (used for the referrer pool).
OWN_ACCOUNTS = {"a": ["9500", "9600", "9700"], "b": ["9800"]}
# "botkey-crash" reproduces what the real (subscription) bot did: the unfiltered ledger fails with an
# HTTP 500 and a non-JSON body, while the documented per-type query is assumed to work; referrals as obj.users.
RAW = "__raw__"
PHP_FATAL = "<br />\n<b>Fatal error</b>:  Uncaught TypeError: explode(): Argument #2 ($string) must be of type string, null given in /var/www/bot/api/users.php:812"
REFERRERS = {"a": {"9001": 40, "9002": 12}, "b": {"9101": 25}}


class BotMock:
    def __init__(self, seed: int = 11, *, stores_unknown_start: bool = False) -> None:
        # Whether a user who starts the bot with an unknown numeric code is recorded under that code (and
        # listed by affiliates_users for it). The real bot's behaviour is what the panel's test finds out.
        self.stores_unknown_start = stores_unknown_start
        self.lock = threading.Lock()
        self.calls: list[tuple[str, str, str]] = []
        rnd = random.Random(seed)
        self.data: dict[str, dict[str, Any]] = {}
        now = datetime.now()
        for key in ("a", "b"):
            users: dict[str, list[dict[str, Any]]] = {}
            txs: list[dict[str, Any]] = []
            uid = 700000 if key == "a" else 800000
            for ref, n in REFERRERS[key].items():
                lst = []
                for _ in range(n):
                    uid += 1
                    reg = now - timedelta(days=rnd.randint(1, 40), minutes=rnd.randint(0, 600))
                    lst.append({"id": uid, "username": f"u{uid}", "register": int(reg.timestamp()), "Balance": 0})
                    if rnd.random() < 0.3:
                        when = reg + timedelta(hours=rnd.randint(1, 48))
                        txs.append(self._tx(len(txs) + 1, uid, "buy", 59000, min(when, now)))
                users[ref] = lst
            for _ in range(160):  # organic sales from users that did not come through a tag
                uid += 1
                when = now - timedelta(days=rnd.randint(0, 59), minutes=rnd.randint(0, 1400))
                typ = rnd.choice(["buy", "buy", "buy_extend", "charge_gateway", "cashback"])
                txs.append(self._tx(len(txs) + 1, uid, typ, rnd.choice([59000, 89000, 119000]), when))
            self.data[key] = {
                "known": set(OWN_ACCOUNTS[key]),
                "users": users,
                "txs": sorted(txs, key=lambda t: t["created_at"], reverse=True),
                "count_user": 1500 if key == "a" else 600,
            }

    @staticmethod
    def _tx(i: int, uid: int, typ: str, amount: int, when: datetime) -> dict[str, Any]:
        return {
            "id": i,
            "id_user": uid,
            "type": typ,
            "source": "bot",
            "amount": amount,
            "created_at": when.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def add_user(self, key: str, referrer: str, buy: bool = True) -> int:
        """Simulate a new user arriving through a referral link (used by the e2e test)."""
        with self.lock:
            d = self.data[key]
            uid = 990000 + sum(len(v) for v in d["users"].values())
            now = datetime.now()
            d["users"].setdefault(referrer, []).append(
                {"id": uid, "username": f"u{uid}", "register": int(now.timestamp())}
            )
            d["count_user"] += 1
            if buy:
                d["txs"].insert(0, self._tx(len(d["txs"]) + 1000, uid, "buy", 59000, now))
            return uid

    def start(self, key: str, code: str) -> int:
        """A brand-new Telegram account opens t.me/<bot>?start=<code>. Returns the new user's id."""
        with self.lock:
            d = self.data[key]
            uid = 970000 + d["count_user"]
            row = {"id": uid, "username": f"u{uid}", "register": int(datetime.now().timestamp())}
            d["count_user"] += 1
            if self._exists(d, code) or self.stores_unknown_start:  # a real referrer: a referral of that user
                d["users"].setdefault(code, []).append(row)
            d["known"].add(str(uid))
            return uid

    @staticmethod
    def _exists(d: dict[str, Any], cid: str) -> bool:
        if cid in d["known"] or cid in d["users"]:
            return True
        return any(str(u["id"]) == cid for rows in d["users"].values() for u in rows)

    def handle(self, token: str, method: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        key = KEYS.get(token)
        if key is None:
            return 403, {"status": False, "msg": "token invalid", "obj": []}
        action = str(body.get("actions") or "")
        with self.lock:
            self.calls.append((key, path, action))
            d = self.data[key]
            if path == "/api/statbot":
                return 200, {"count_user": d["count_user"], "count_invoice": len(d["txs"]), "count_agent": 3}
            if path == "/api/settings" and action == "setting_info":
                return 200, {
                    "status": True,
                    "msg": "Successful",
                    "obj": {"username_bot": BOT_NAMES[key], "status": "on"},
                }
            if path == "/api/invoice" and action == "invoices":
                return 200, {"status": True, "msg": "Successful", "obj": []}
            if path != "/api/users":
                return 200, {"status": False, "msg": "Action Invalid", "obj": []}
            if token == "botkey-limited":
                return 403, {"status": False, "msg": "access denied", "obj": []}
            if token == "botkey-500":
                return 500, {RAW: PHP_FATAL}
            if token == "botkey-echo":
                return 500, {RAW: f"<b>Fatal error</b>: bad request, headers: Token: {token}; key={token}"}
            if method != "GET":
                return 200, {"status": False, "msg": "method invalid", "obj": []}
            page = max(1, int(body.get("page") or 1))
            limit = max(1, min(int(body.get("limit") or 50), 1000))
            if action == "transactions":
                rows = d["txs"]
                if token == "botkey-crash":
                    if not body.get("type"):
                        return 500, {RAW: PHP_FATAL}
                    rows = [t for t in rows if t["type"] == body["type"]]
                if body.get("from"):
                    lo = int(body["from"])
                    rows = [
                        t
                        for t in rows
                        if int(datetime.strptime(t["created_at"], "%Y-%m-%d %H:%M:%S").timestamp()) >= lo
                    ]
                chunk = rows[(page - 1) * limit : page * limit]
                return 200, {
                    "status": True,
                    "msg": "Successful",
                    "obj": {"transactions": chunk, "total": len(rows), "page": page},
                }
            if action == "affiliates_users":
                rows = d["users"].get(str(body.get("chat_id")), [])
                if token == "botkey-crash":  # real shape: all rows, ids only, paging ignored
                    return 200, {
                        "status": True,
                        "msg": "Successful",
                        "obj": {"users": [{"user_id": u["id"]} for u in rows]},
                    }
                return 200, {"status": True, "msg": "Successful", "obj": rows[(page - 1) * limit : page * limit]}
            if action == "user":
                cid = str(body.get("chat_id"))
                if self._exists(d, cid):
                    return 200, {
                        "status": True,
                        "msg": "Successful",
                        "obj": {"user": {"id": int(cid), "affiliatescount": len(d["users"].get(cid, []))}},
                    }
                return 200, {"status": False, "msg": "user not found", "obj": []}
            return 200, {"status": False, "msg": "Action Invalid", "obj": []}
