"""The Telegram Ads API, method by method, as documented at ads.telegram.org/docs/api (Sept 18, 2026 version).

`TgAdsApi` is mixed into `TgAdsMock` (tgads_mock.py, which holds the made-up accounts and the test hooks).
Everything the documentation states is enforced here: request formats (query string, form, JSON,
multipart), case-insensitive method names, idempotency keys (header or parameter, replay header, parameter
mismatch, 24 hours, only once the method started executing), required parameters, lengths and ranges,
photo/video rules, targets, statuses (an edited or new ad without budget stays "ready_for_review"),
budgets, inactivity before decreaseAdBudget/deleteAd, related accounts, audiences, pixels and target lists.

Error codes: the documentation names only AD_TITLE_REQUIRED and a few others in passing, so the rest follow
the same pattern (PARAM_REQUIRED, PARAM_INVALID, ...). Tests check that a call fails, and the code where the
panel depends on it.
"""

import calendar
import hashlib
import json
import re
import time
from typing import Any
from urllib.parse import parse_qsl

MIN_INITIAL_BUDGET = 1.0  # observed on real accounts (the documentation only says "defaults to 0")
MIN_CPM = 0.1
IDEM_TTL = 24 * 3600
PLACEMENTS = ("channel_post", "bot_banner", "search_result", "video_banner")
PLACEMENT_OF = {"channels": "channel_post", "users": "channel_post", "bots": "bot_banner", "search": "search_result"}
BUTTONS = (
    "subscribe", "view", "read", "learn_more", "download", "open", "sign_up", "buy", "order", "play", "try",
    "leave_request",
)  # fmt: skip
DEVICES = ("ios", "android", "mobile", "desktop")
PIXEL_EVENT_TYPES = (
    "page_view", "add_to_cart", "add_to_wishlist", "customize_product", "initiate_checkout", "add_payment_info",
    "purchase", "contact", "lead", "schedule", "complete_registration", "submit_application", "start_trial",
    "subscribe", "view_content", "search", "find_location", "donate", "custom",
)  # fmt: skip
TIMEZONES = {
    -43200, -39600, -36000, -34200, -32400, -28800, -25200, -21600, -18000, -14400, -12600, -10800, -9000,
    -7200, -3600, 0, 3600, 7200, 10800, 12600, 14400, 16200, 18000, 19800, 20700, 21600, 23400, 25200, 28800,
    31500, 32400, 34200, 36000, 37800, 39600, 43200, 45900, 46800, 49500, 50400,
}  # fmt: skip
LANGUAGES = [{"language_code": c, "name": n} for c, n in (("en", "English"), ("fa", "Persian"), ("ru", "Russian"))]
TOPICS = [{"topic_id": i, "name": n} for i, n in ((1, "Technology"), (2, "News"), (3, "Crypto"), (4, "Education"))]
COUNTRIES = [{"country_code": c, "name": n} for c, n in (("IR", "Iran"), ("DE", "Germany"), ("US", "United States"))]
LOCATIONS = [
    {"location_id": 101, "name": "Tehran", "country_code": "IR", "region": "Tehran"},
    {"location_id": 102, "name": "Mashhad", "country_code": "IR", "region": "Razavi Khorasan"},
    {"location_id": 103, "name": "Tabriz", "country_code": "IR", "region": "East Azerbaijan"},
    {"location_id": 201, "name": "Berlin", "country_code": "DE", "region": "Berlin"},
]
# Fields whose change sends an ad to review (the title is only shown in the Telegram Ads interface).
REVIEWED = ("text", "photo", "video", "promote_url", "website_name", "website_photo", "button", "additional_info")
METHODS = [
    "createAccount",
    "editAccountInfo",
    "getAccountInfo",
    "getRelatedAccountsList",
    "increaseAccountBudget",
    "decreaseAccountBudget",
    "getTransactionStatus",
    "getCurrentAccount",
    "getAccountsById",
    "getAccountTransactionsList",
    "getAccountStats",
    "getAccountReport",
    "uploadAdPhoto",
    "uploadAdVideo",
    "uploadWebsitePhoto",
    "createAd",
    "editAd",
    "editAdStatus",
    "increaseAdBudget",
    "decreaseAdBudget",
    "submitAdForReview",
    "deleteAd",
    "getAdsById",
    "getAdsList",
    "getAdTransactionsList",
    "getAdStats",
    "createAudience",
    "editAudience",
    "deleteAudience",
    "getAudiencesById",
    "getAudiencesList",
    "createPixel",
    "getPixel",
    "createPixelEvent",
    "editPixelEvent",
    "deletePixelEvent",
    "getPixelEventsById",
    "getPixelEventsList",
    "getTargetLanguagesList",
    "getTargetTopicsList",
    "getTargetCountriesList",
    "getTargetLocationsById",
    "getTargetLocationsList",
    "getTargetChannel",
    "getTargetBot",
]
CANONICAL = {m.lower(): m for m in METHODS}


class ApiError(Exception):
    """ok=false. `executed`: the method had started (a key stores the answer); validation errors do not."""

    def __init__(self, code: str, executed: bool = False) -> None:
        super().__init__(code)
        self.code, self.executed = code, executed


# ---------------------------------------------------------------------------------------------- requests


def parse_request(http_method: str, query: str, ctype: str, body: bytes) -> tuple[dict, dict, str]:
    """(params, files, format) for a request in any of the documented forms; query string values merge in."""
    params: dict[str, Any] = dict(parse_qsl(query, keep_blank_values=True))
    files: dict[str, bytes] = {}
    fmt = "query"
    ctype = (ctype or "").lower()
    if http_method == "POST" and body:
        if ctype.startswith("multipart/"):
            p, files = parse_multipart(body, ctype)
            params.update(p)
            fmt = "multipart"
        elif ctype.startswith("application/x-www-form-urlencoded"):
            params.update(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
            fmt = "form"
        else:
            params.update(json.loads(body.decode("utf-8")))
            fmt = "json"
    return params, files, fmt


def parse_multipart(body: bytes, ctype: str) -> tuple[dict, dict]:
    boundary = ctype.split("boundary=")[1].split(";")[0].strip('"').encode()
    params, files = {}, {}
    for part in body.split(b"--" + boundary):
        if b"Content-Disposition" not in part:
            continue
        head, _, data = part.partition(b"\r\n\r\n")
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        if b"filename=" in head:
            files[name] = data[:-2] if data.endswith(b"\r\n") else data
        else:
            params[name] = data.rstrip(b"\r\n").decode()
    return params, files


# ---------------------------------------------------------------------------------------------- params


def _given(p: dict, k: str) -> bool:
    return k in p and p[k] is not None and p[k] != ""


def _str(p: dict, k: str, code: str, *, req: bool = False, lo: int = 0, hi: int = 10**6, nbytes: bool = False):
    if not _given(p, k):
        if req:
            raise ApiError(f"{code}_REQUIRED")
        return None
    v = str(p[k])
    n = len(v.encode("utf-8")) if nbytes else len(v)
    if n < lo:
        raise ApiError(f"{code}_REQUIRED" if n == 0 else f"{code}_INVALID")
    if n > hi:
        raise ApiError(f"{code}_TOO_LONG")
    return v


def _int(p: dict, k: str, code: str, *, req: bool = False, lo: int | None = None, hi: int | None = None):
    if not _given(p, k):
        if req:
            raise ApiError(f"{code}_REQUIRED")
        return None
    try:
        v = int(p[k]) if not isinstance(p[k], bool) else None
    except (TypeError, ValueError):
        v = None
    if v is None or (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ApiError(f"{code}_INVALID")
    return v


def _float(p: dict, k: str, code: str, *, req: bool = False, lo: float | None = None):
    if not _given(p, k):
        if req:
            raise ApiError(f"{code}_REQUIRED")
        return None
    try:
        v = float(p[k])
    except (TypeError, ValueError):
        raise ApiError(f"{code}_INVALID") from None
    if lo is not None and v < lo:
        raise ApiError(f"{code}_INVALID")
    return v


def _bool(p: dict, k: str) -> bool | None:
    if not _given(p, k):
        return None
    v = p[k]
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1")


def _json(p: dict, k: str, code: str, kind: type, *, req: bool = False):
    """A JSON-serialized value (or the value itself in a JSON body)."""
    if not _given(p, k):
        if req:
            raise ApiError(f"{code}_REQUIRED")
        return None
    v = p[k]
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            raise ApiError(f"{code}_INVALID") from None
    if kind is bool and v is False:
        return False
    if not isinstance(v, kind):
        raise ApiError(f"{code}_INVALID")
    return v


def _page(items: list, p: dict, *, default: int | None = 100, max_limit: int | None = None) -> tuple[list, str | None]:
    offset = _int(p, "offset", "OFFSET", lo=0) or 0
    limit = _int(p, "limit", "LIMIT", lo=0, hi=max_limit)
    if limit is None:
        limit = default if default is not None else len(items)
    chunk = items[offset : offset + limit]
    nxt = str(offset + limit) if offset + limit < len(items) else None
    return chunk, nxt


def _channel_num(username: str) -> int:
    return int(hashlib.sha256(username.lower().encode()).hexdigest()[:12], 16)


def _phone_hash(v: str) -> str:
    v = str(v).strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", v):
        return v.lower()
    digits = re.sub(r"\D", "", v)
    if not 7 <= len(digits) <= 15:
        raise ApiError("PHONE_NUMBER_INVALID")
    return hashlib.sha256(digits.encode()).hexdigest()


def _is_tg_link(url: str) -> bool:
    return bool(re.match(r"^(https?://)?(t\.me|telegram\.me)/", url)) or url.startswith("@")


# ---------------------------------------------------------------------------------------------- the API


class TgAdsApi:
    """Mixed into TgAdsMock, which provides: accounts, lock, calls, inactive_seconds, supports_delete,
    supports_photo_edit, ignores_edits, ignored_formats, edits_wait_for_review, in_progress_keys, _to_review."""

    accounts: dict[str, dict[str, Any]]
    calls: list[tuple[str, str]]
    inactive_seconds: float
    supports_delete: bool
    supports_photo_edit: bool
    ignores_edits: bool
    ignored_formats: set[str]
    edits_wait_for_review: bool
    in_progress_keys: set[str]

    # -------------------------------------------------------------- entry point
    def respond(
        self,
        token: str,
        method: str,
        params: dict[str, Any],
        files: dict[str, bytes] | None = None,
        headers: dict[str, str] | None = None,
        fmt: str = "json",
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """One API call: (JSON body, extra response headers)."""
        from tests.mocks.tgads_mock import TOKENS

        files = files or {}
        params = dict(params)
        key = TOKENS.get(token)
        if key is None:
            return {"ok": False, "error": "ACCESS_TOKEN_INVALID" if token else "ACCESS_TOKEN_REQUIRED"}, {}
        name = CANONICAL.get(method.strip("/").lower())
        idem = (headers or {}).get("Idempotency-Key") or params.pop("idempotency_key", None)
        if idem is not None and len(str(idem)) > 128:
            return {"ok": False, "error": "IDEMPOTENCY_KEY_INVALID"}, {}
        with self.lock:
            self.calls.append((key, name or method))
            st = self.accounts[key]
            if name is None:
                return {"ok": False, "error": "METHOD_NOT_FOUND"}, {}
            store = st["idem"]
            now = time.time()
            for k in [k for k, v in store.items() if now - v["ts"] > IDEM_TTL]:
                del store[k]
            fp = json.dumps([name, sorted((k, str(v)) for k, v in params.items())], ensure_ascii=False)
            if idem:
                if idem in self.in_progress_keys:
                    return {"ok": False, "error": "IDEMPOTENT_REQUEST_IN_PROGRESS"}, {}
                if idem in store:
                    if store[idem]["fp"] != fp:
                        return {"ok": False, "error": "IDEMPOTENT_PARAM_MISMATCH"}, {}
                    return store[idem]["res"], {"Idempotent-Replayed": "true"}
            try:
                res: dict[str, Any] = {"ok": True, "result": self._dispatch(st, name, params, files, fmt)}
                executed = True
            except ApiError as e:
                res, executed = {"ok": False, "error": e.code}, e.executed
            except KeyError as e:  # legacy hooks of the mock
                res, executed = {"ok": False, "error": str(e.args[0])}, False
            if idem and executed:
                store[idem] = {"fp": fp, "res": res, "ts": now}
            return res, {}

    def handle(
        self, token: str, method: str, params: dict[str, Any], files: dict[str, bytes], idem_key: str | None
    ) -> dict[str, Any]:
        """Old entry point (JSON body, key in a header)."""
        return self.respond(token, method, params, files, {"Idempotency-Key": idem_key} if idem_key else {})[0]

    def _dispatch(self, st: dict, name: str, p: dict, files: dict, fmt: str) -> Any:
        if name in ("deleteAd",) and not self.supports_delete:
            raise ApiError("METHOD_NOT_FOUND")
        fn = getattr(self, "m_" + name)
        if name in ("uploadAdPhoto", "uploadAdVideo", "uploadWebsitePhoto"):
            return fn(st, p, files)
        if name in ("editAd",):
            return fn(st, p, fmt)
        return fn(st, p)

    # -------------------------------------------------------------- helpers
    def _acct(self, st: dict, p: dict, *, req: bool = False) -> dict:
        aid = _str(p, "account_id", "ACCOUNT_ID", req=req)
        if aid is None or aid == st["account"]["account_id"]:
            if req:
                raise ApiError("ACCOUNT_ID_INVALID")  # these methods act on a related account
            return st
        rel = st.setdefault("related", {}).get(aid)
        if rel is None:
            raise ApiError("ACCOUNT_ID_INVALID")
        return rel

    def _ad(self, st: dict, p: dict) -> dict:
        aid = _int(p, "ad_id", "AD_ID", req=True)
        a = st["ads"].get(aid)
        if a is None:
            raise ApiError("AD_ID_INVALID")
        return a

    def _out_ad(self, a: dict, p: dict) -> dict:
        out = {k: v for k, v in a.items() if not k.startswith("_")}
        if not _bool(p, "return_target"):
            out.pop("target", None)
        return out

    def _inactive_for(self, st: dict, a: dict) -> float:
        if a["status"] == "active" and not a.get("is_paused"):
            return 0.0
        since = st["paused_at"].get(a["ad_id"], st.setdefault("inactive_since", {}).get(a["ad_id"], 0.0))
        return time.time() - since

    def _mark_inactive(self, st: dict, a: dict) -> None:
        st.setdefault("inactive_since", {}).setdefault(a["ad_id"], time.time())

    def _review(self, a: dict) -> None:
        a["status"] = "in_review" if a["remaining_budget"] > 0 else "ready_for_review"
        a.pop("decline_reason", None)

    def _photo_ok(self, st: dict, pid: str, kind: str) -> None:
        if pid not in st.setdefault("uploads", {}).setdefault(kind, set()):
            raise ApiError(f"{kind.upper()}_ID_INVALID")

    def _ad_tx(self, st: dict, ad_id: int, amount: float) -> None:
        now = int(time.time())
        st.setdefault("ad_tx", {}).setdefault(ad_id, []).append(
            {"peer": {"type": "account"}, "date": now, "currency": "TON", "amount": round(amount, 5)}
        )
        a = st["ads"].get(ad_id, {})
        st.setdefault("acct_tx", []).append(
            {
                "peer": {"type": "ad", "ad_id": ad_id, "ad_title": a.get("title", "")},
                "date": now,
                "currency": "TON",
                "amount": round(-amount, 5),
            }
        )

    # -------------------------------------------------------------- accounts
    def _info(self, st: dict) -> dict:
        acc = st["account"]
        info = st.setdefault(
            "info",
            {
                "full_name": acc["title"] + " LLC",
                "email": "ads@example.invalid",
                "phone_number": "+10000000000",
                "country": "Testland",
                "city": "Mock City",
            },
        )
        return {"account_id": acc["account_id"], "title": acc["title"], **info}

    def m_createAccount(self, st: dict, p: dict) -> dict:
        if st.get("is_related"):
            raise ApiError("ACCOUNT_NOT_MAIN")
        title = _str(p, "title", "ACCOUNT_TITLE", req=True, lo=1, hi=128)
        info = {
            "full_name": _str(p, "full_name", "FULL_NAME", req=True, lo=1, hi=256),
            "email": _str(p, "email", "EMAIL", req=True, lo=1, hi=64),
            "phone_number": _str(p, "phone_number", "PHONE_NUMBER", req=True, lo=1, hi=32),
            "country": _str(p, "country", "COUNTRY", req=True, lo=1, hi=128),
            "city": _str(p, "city", "CITY", req=True, lo=1, hi=128),
        }
        if "@" not in str(info["email"]):
            raise ApiError("EMAIL_INVALID")
        legal = _str(p, "legal_name", "LEGAL_NAME", hi=256)
        if legal:
            info["legal_name"] = legal
        related = st.setdefault("related", {})
        aid = f"{st['account']['account_id']}-r{len(related) + 1}"
        related[aid] = {
            "is_related": True,
            "account": {
                "account_id": aid,
                "title": title,
                "currency": "TON",
                "spent_budget": 0.0,
                "remaining_budget": 0.0,
                "ads_budget": 0.0,
            },
            "info": info,
            "ads": {},
            "stats": {},
            "paused_at": {},
            "idem": {},
            "next_id": 1,
        }
        return self._info(related[aid])

    def m_editAccountInfo(self, st: dict, p: dict) -> dict:
        acc = self._acct(st, p)
        title = _str(p, "title", "ACCOUNT_TITLE", lo=1, hi=128)
        info = self._info(acc)
        if title:
            acc["account"]["title"] = title
        for k, code, hi in (
            ("full_name", "FULL_NAME", 256),
            ("email", "EMAIL", 64),
            ("phone_number", "PHONE_NUMBER", 32),
            ("country", "COUNTRY", 128),
            ("city", "CITY", 128),
        ):
            v = _str(p, k, code, lo=1, hi=hi)
            if v is not None:
                acc["info"][k] = v
        if "legal_name" in p:
            acc["info"]["legal_name"] = _str(p, "legal_name", "LEGAL_NAME", hi=256) or ""
        del info
        return self._info(acc)

    def m_getAccountInfo(self, st: dict, p: dict) -> dict:
        return self._info(self._acct(st, p))

    def m_getRelatedAccountsList(self, st: dict, p: dict) -> dict:
        rel = [r["account"] for r in st.get("related", {}).values()]
        chunk, _ = _page(rel, p, default=None)
        return {"total_count": len(rel), "accounts": chunk}

    def _transfer(self, st: dict, p: dict, *, increase: bool) -> dict:
        rel = self._acct(st, p, req=True)
        amount = _float(p, "amount", "AMOUNT", req=True)
        if amount is None or amount <= 0:
            raise ApiError("AMOUNT_INVALID")
        src, dst = (st, rel) if increase else (rel, st)
        txs = st.setdefault("transactions", {})
        tx = {
            "transaction_id": f"tx{len(txs) + 1}",
            "currency": "TON",
            "amount": round(amount if increase else -amount, 5),
            "main_account": st["account"],
            "related_account": rel["account"],
        }
        if src["account"]["remaining_budget"] + 1e-9 < amount:
            tx |= {"status": "failed", "error": "NOT_ENOUGH_BUDGET"}
        else:
            src["account"]["remaining_budget"] = round(src["account"]["remaining_budget"] - amount, 5)
            dst["account"]["remaining_budget"] = round(dst["account"]["remaining_budget"] + amount, 5)
            tx["status"] = "completed"
            now = int(time.time())
            st.setdefault("acct_tx", []).append(
                {
                    "peer": {
                        "type": "related_account",
                        "account_id": rel["account"]["account_id"],
                        "account_title": rel["account"]["title"],
                    },
                    "date": now,
                    "currency": "TON",
                    "amount": round(-amount if increase else amount, 5),
                }
            )
            rel.setdefault("acct_tx", []).append(
                {"peer": {"type": "main_account"}, "date": now, "currency": "TON", "amount": tx["amount"]}
            )
        txs[tx["transaction_id"]] = tx
        return tx

    def m_increaseAccountBudget(self, st: dict, p: dict) -> dict:
        return self._transfer(st, p, increase=True)

    def m_decreaseAccountBudget(self, st: dict, p: dict) -> dict:
        return self._transfer(st, p, increase=False)

    def m_getTransactionStatus(self, st: dict, p: dict) -> dict:
        tid = _str(p, "transaction_id", "TRANSACTION_ID", req=True)
        tx = st.get("transactions", {}).get(tid)
        if tx is None:
            raise ApiError("TRANSACTION_ID_INVALID")
        return tx

    def m_getCurrentAccount(self, st: dict, p: dict) -> dict:
        return st["account"]

    def m_getAccountsById(self, st: dict, p: dict) -> list:
        ids = _json(p, "account_ids", "ACCOUNT_IDS", list, req=True)
        known = {st["account"]["account_id"]: st["account"]} | {
            k: v["account"] for k, v in st.get("related", {}).items()
        }
        return [known[str(i)] for i in ids if str(i) in known]

    def m_getAccountTransactionsList(self, st: dict, p: dict) -> dict:
        acc = self._acct(st, p)
        txs = sorted(acc.get("acct_tx", []), key=lambda t: -t["date"])
        chunk, nxt = _page(txs, p)
        out: dict[str, Any] = {"total_count": len(txs), "transactions": chunk}
        if nxt:
            out["next_offset"] = nxt
        return out

    def _stat_items(self, rows: list[dict], p: dict) -> list[dict]:
        interval = _int(p, "interval", "INTERVAL", req=True)
        if interval not in (300, 86400):
            raise ApiError("INTERVAL_INVALID")
        frm = _int(p, "from_time", "FROM_TIME", req=True, lo=0)
        to = _int(p, "to_time", "TO_TIME", req=True, lo=0)
        assert frm is not None and to is not None
        frm, to = frm - frm % interval, to - to % interval
        if to < frm:
            raise ApiError("TO_TIME_INVALID")
        if to - frm > 1000 * interval:
            raise ApiError("PERIOD_TOO_LONG")
        buckets: dict[int, dict] = {}
        for r in rows:
            ts = calendar.timegm(time.strptime(r["date"], "%Y-%m-%d"))
            if frm <= ts < to:
                b = buckets.setdefault(ts - ts % interval, {"views": 0, "clicks": 0, "actions": 0, "spent": 0.0})
                for k in ("views", "clicks", "actions", "spent"):
                    b[k] += r[k]
        return [
            {
                "from_time": t,
                "to_time": t + interval,
                "views": b["views"],
                "opens": 0,
                "clicks": b["clicks"],
                "actions": b["actions"],
                "currency": "TON",
                "spent_budget": round(b["spent"], 5),
            }
            for t, b in sorted(buckets.items())
        ]

    def m_getAccountStats(self, st: dict, p: dict) -> list:
        acc = self._acct(st, p)
        rows = [r for rs in acc["stats"].values() for r in rs]
        return self._stat_items(rows, p)

    def m_getAccountReport(self, st: dict, p: dict) -> list:
        acc = self._acct(st, p)
        year = _int(p, "year", "YEAR", req=True, lo=2021, hi=2100)
        month = _int(p, "month", "MONTH", req=True, lo=1, hi=12)
        prefix = f"{year:04d}-{month:02d}-"
        out = []
        for aid, rows in acc["stats"].items():
            rs = [r for r in rows if r["date"].startswith(prefix)]
            if not rs:
                continue
            a = acc["ads"].get(aid)
            item = {
                "ad_id": aid,
                "ad_title": a["title"] if a else "",
                "views": sum(r["views"] for r in rs),
                "opens": 0,
                "clicks": sum(r["clicks"] for r in rs),
                "actions": sum(r["actions"] for r in rs),
                "currency": "TON",
                "spent_budget": round(sum(r["spent"] for r in rs), 5),
            }
            if a is None:
                item["ad_is_deleted"] = True
            out.append(item)
        return out

    # -------------------------------------------------------------- uploads
    def _upload(self, st: dict, p: dict, files: dict, kind: str, limit: int, kinds: tuple[bytes, ...]) -> dict:
        acc = self._acct(st, p)
        data = files.get("file")
        if not data:
            raise ApiError("FILE_REQUIRED")
        if len(data) > limit:
            raise ApiError("FILE_TOO_BIG")
        if not any(data.startswith(m) if m != b"ftyp" else m in data[:16] for m in kinds):
            raise ApiError("FILE_TYPE_INVALID")
        uploads = acc.setdefault("uploads", {}).setdefault(kind, set())
        fid = {"photo": "ph_", "video": "vd_", "website_photo": "wp_"}[kind] + hashlib.sha256(data).hexdigest()[:10]
        uploads.add(fid)
        if kind == "video":
            return {"video_id": fid, "video_url": f"https://example.invalid/{fid}.mp4"}
        return {"photo_id": fid, "photo_url": f"https://example.invalid/{fid}.jpg"}

    def m_uploadAdPhoto(self, st: dict, p: dict, files: dict) -> dict:
        return self._upload(st, p, files, "photo", 5 * 2**20, (b"\xff\xd8", b"\x89PNG"))

    def m_uploadAdVideo(self, st: dict, p: dict, files: dict) -> dict:
        return self._upload(st, p, files, "video", 20 * 2**20, (b"ftyp",))

    def m_uploadWebsitePhoto(self, st: dict, p: dict, files: dict) -> dict:
        return self._upload(st, p, files, "website_photo", 2**20, (b"\xff\xd8", b"\x89PNG"))

    # -------------------------------------------------------------- targets
    def _resolve(self, st: dict, kind: str, ident: Any) -> dict:
        """@username, or a numeric id this account resolved before (Sept 2026 rule)."""
        resolved = st.setdefault("resolved", {}).setdefault(kind, {})
        s = str(ident).strip()
        bad = "CHANNEL_INVALID" if kind == "channels" else "BOT_INVALID"
        if s.lstrip("-").isdigit():
            if int(s) not in resolved:
                raise ApiError(bad)
            username = resolved[int(s)]
        else:
            username = s.lstrip("@")
            if not re.fullmatch(r"[A-Za-z0-9_]{3,64}", username) or "bad" in username.lower():
                raise ApiError(bad)
        num = _channel_num(username)
        resolved[num] = username
        idk = "channel_id" if kind == "channels" else "bot_id"
        return {idk: num, "title": username, "username": username, "photo_url": f"https://example.invalid/{num}.jpg"}

    def m_getTargetChannel(self, st: dict, p: dict) -> dict:
        _bool(p, "for_excluding")
        return self._resolve(st, "channels", _str(p, "channel_id", "CHANNEL_ID", req=True))

    def m_getTargetBot(self, st: dict, p: dict) -> dict:
        return self._resolve(st, "bots", _str(p, "bot_id", "BOT_ID", req=True))

    def m_getTargetLanguagesList(self, st: dict, p: dict) -> list:
        return LANGUAGES

    def m_getTargetTopicsList(self, st: dict, p: dict) -> list:
        return TOPICS

    def m_getTargetCountriesList(self, st: dict, p: dict) -> list:
        return COUNTRIES

    def m_getTargetLocationsById(self, st: dict, p: dict) -> list:
        ids = _json(p, "location_ids", "LOCATION_IDS", list, req=True)
        return [loc for loc in LOCATIONS if loc["location_id"] in {int(i) for i in ids}]

    def m_getTargetLocationsList(self, st: dict, p: dict) -> dict:
        cc = _str(p, "country_code", "COUNTRY_CODE", req=True)
        if not re.fullmatch(r"[A-Z]{2}", cc or "") or cc not in {c["country_code"] for c in COUNTRIES}:
            raise ApiError("COUNTRY_CODE_INVALID")
        q = (_str(p, "query", "QUERY", req=True) or "").lower()
        found = [loc for loc in LOCATIONS if loc["country_code"] == cc and q in loc["name"].lower()]
        chunk, nxt = _page(found, p)
        return {"total_count": len(found), "locations": chunk, "next_offset": nxt or ""}

    def _target(self, st: dict, raw: Any) -> tuple[dict, dict]:
        """InputAdTarget -> (stored AdTarget, summary used by other rules)."""
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raise ApiError("TARGET_INVALID") from None
        if not isinstance(raw, dict) or raw.get("type") not in ("channels", "users", "bots", "search"):
            raise ApiError("TARGET_INVALID")
        t = raw["type"]
        lst = lambda k: raw.get(k) or []  # noqa: E731
        if t == "channels":
            langs, topics, ex_topics = lst("language_codes"), lst("topic_ids"), lst("exclude_topic_ids")
            chans, ex_chans = lst("channel_ids"), lst("exclude_channel_ids")
            if len(langs) > 8 or len(topics) + len(ex_topics) > 20 or len(chans) + len(ex_chans) > 100:
                raise ApiError("TARGET_INVALID")
            if chans and (langs or topics):
                raise ApiError("TARGET_INVALID")
            if topics and len(langs) != 1:
                raise ApiError("TARGET_INVALID")
            if not (chans or langs or topics):
                raise ApiError("TARGET_EMPTY")
            known = {x["topic_id"]: x for x in TOPICS}
            if any(int(i) not in known for i in topics + ex_topics):
                raise ApiError("TOPIC_ID_INVALID")
            return {
                "type": "channels",
                "languages": [x for x in LANGUAGES if x["language_code"] in langs],
                "topics": [known[int(i)] for i in topics],
                "exclude_topics": [known[int(i)] for i in ex_topics],
                "channels": [self._resolve(st, "channels", c) for c in chans],
                "exclude_channels": [self._resolve(st, "channels", c) for c in ex_chans],
            }, {"type": t}
        if t == "users":
            if "country_codes" not in raw:
                raise ApiError("COUNTRY_CODES_REQUIRED")
            ccs, locs = lst("country_codes"), lst("location_ids")
            if len(ccs) > 8 or len(locs) > 20:
                raise ApiError("TARGET_INVALID")
            if any(c not in {x["country_code"] for x in COUNTRIES} for c in ccs):
                raise ApiError("COUNTRY_CODE_INVALID")
            if locs:
                byid = {x["location_id"]: x for x in LOCATIONS}
                if len(ccs) != 1 or any(int(i) not in byid or byid[int(i)]["country_code"] != ccs[0] for i in locs):
                    raise ApiError("LOCATION_ID_INVALID")
            if len(lst("language_codes")) > 8 or len(lst("topic_ids")) + len(lst("exclude_topic_ids")) > 20:
                raise ApiError("TARGET_INVALID")
            if len(lst("channel_ids")) + len(lst("exclude_channel_ids")) > 100:
                raise ApiError("TARGET_INVALID")
            aud, ex_aud = lst("audience_ids"), lst("exclude_audience_ids")
            if len(aud) > 4 or len(ex_aud) > 4 or len(aud) + len(ex_aud) > 10:
                raise ApiError("TARGET_INVALID")
            known = st.setdefault("audiences", {})
            if any(int(a) not in known for a in aud + ex_aud):
                raise ApiError("AUDIENCE_ID_INVALID")
            if raw.get("device") and raw["device"] not in DEVICES:
                raise ApiError("DEVICE_INVALID")
            if raw.get("exclude_political_channels") and raw.get("political_channels_only"):
                raise ApiError("TARGET_INVALID")
            return {
                "type": "users",
                "countries": [x for x in COUNTRIES if x["country_code"] in ccs],
                "locations": [x for x in LOCATIONS if x["location_id"] in {int(i) for i in locs}],
                "audience_ids": [int(a) for a in aud],
                "exclude_audience_ids": [int(a) for a in ex_aud],
                "channels": [self._resolve(st, "channels", c) for c in lst("channel_ids")],
                **({"device": raw["device"]} if raw.get("device") else {}),
            }, {"type": t}
        if t == "bots":
            bots = lst("bot_ids")
            if len(bots) > 100:
                raise ApiError("TARGET_INVALID")
            if not bots:
                raise ApiError("TARGET_EMPTY")
            return {"type": "bots", "bots": [self._resolve(st, "bots", b) for b in bots]}, {"type": t}
        qs = lst("search_queries")
        if not qs or len(qs) > 10:
            raise ApiError("SEARCH_QUERIES_INVALID")
        return {"type": "search", "search_queries": [str(q) for q in qs]}, {"type": t}

    # -------------------------------------------------------------- ads
    def _dates(self, a: dict, p: dict) -> None:
        now = time.time()
        act = _int(p, "activate_date", "ACTIVATE_DATE")
        deact = _int(p, "deactivate_date", "DEACTIVATE_DATE")
        for v, code in ((act, "ACTIVATE_DATE"), (deact, "DEACTIVATE_DATE")):
            if v is not None and not now < v <= now + 365 * 86400:
                raise ApiError(f"{code}_INVALID")
        if act is not None or deact is not None:
            if act is not None:
                a["activate_date"] = act - act % 60
            if deact is not None:
                a["deactivate_date"] = deact - deact % 60
            a["is_paused"] = act is not None
            return
        paused = _bool(p, "is_paused")
        if paused is not None:
            a["is_paused"] = paused
            a.pop("activate_date", None)
            a.pop("deactivate_date", None)

    def _schedule(self, a: dict, p: dict, target_type: str) -> None:
        if "schedule" not in p:
            return
        s = p["schedule"]
        if isinstance(s, str):
            s = False if s.lower() == "false" else json.loads(s)
        if s is False:
            a.pop("schedule", None)
            return
        if not isinstance(s, dict):
            raise ApiError("SCHEDULE_INVALID")
        mask = s.get("week_hours_mask")
        if not (
            isinstance(mask, list) and len(mask) == 7 and all(isinstance(x, int) and 0 <= x <= 16777215 for x in mask)
        ):
            raise ApiError("SCHEDULE_INVALID")
        viewer = bool(s.get("use_viewer_timezone"))
        if viewer and target_type != "users":
            raise ApiError("SCHEDULE_INVALID")
        if not viewer and s.get("timezone") not in TIMEZONES:
            raise ApiError("SCHEDULE_TIMEZONE_INVALID")
        a["schedule"] = {
            "week_hours_mask": mask,
            **({"use_viewer_timezone": True} if viewer else {"timezone": s["timezone"]}),
        }

    def _media(self, st: dict, a: dict, p: dict, target_type: str) -> None:
        pid = _str(p, "photo_id", "PHOTO_ID")
        vid = _str(p, "video_id", "VIDEO_ID")
        if pid and vid:
            raise ApiError("MEDIA_INVALID")
        if (pid or vid) and target_type not in ("channels", "users"):
            raise ApiError("MEDIA_NOT_ALLOWED")
        if pid:
            self._photo_ok(st, pid, "photo")
            a["photo"] = {"photo_id": pid, "photo_url": f"https://example.invalid/{pid}.jpg"}
            a.pop("video", None)
        if vid:
            self._photo_ok(st, vid, "video")
            a["video"] = {"video_id": vid, "video_url": f"https://example.invalid/{vid}.mp4"}
            a.pop("photo", None)

    def _website(self, st: dict, a: dict, p: dict, url: str, *, creating: bool) -> None:
        external = not _is_tg_link(url)
        name = _str(p, "website_name", "WEBSITE_NAME", lo=1, hi=40)
        if external and creating and not name:
            raise ApiError("WEBSITE_NAME_REQUIRED")
        if name:
            a["website_name"] = name
        wp = _str(p, "website_photo_id", "WEBSITE_PHOTO_ID")
        if wp:
            self._photo_ok(st, wp, "website_photo")
            a["website_photo"] = {"photo_id": wp, "photo_url": f"https://example.invalid/{wp}.jpg"}
        button = _str(p, "button", "BUTTON")
        if button:
            if button not in BUTTONS or not external:
                raise ApiError("BUTTON_INVALID")
            a["button"] = button
        conv = _str(p, "conversion_event_id", "CONVERSION_EVENT_ID")
        if conv:
            if a.get("conversion_event_id") and a["conversion_event_id"] != conv:
                raise ApiError("CONVERSION_EVENT_ID_NOT_MODIFIABLE")
            if not external or conv not in st.get("pixel_events", {}):
                raise ApiError("CONVERSION_EVENT_ID_INVALID")
            a["conversion_event_id"] = conv
        if "additional_info" in p:
            info = _str(p, "additional_info", "ADDITIONAL_INFO", hi=64) or ""
            if a.get("additional_info") and a["additional_info"] != info:
                raise ApiError("ADDITIONAL_INFO_NOT_MODIFIABLE")
            if info:
                a["additional_info"] = info

    def m_createAd(self, st: dict, p: dict) -> dict:
        acc = self._acct(st, p)
        title = _str(p, "title", "AD_TITLE", req=True, lo=1, hi=128, nbytes=True)
        if "target" not in p:
            raise ApiError("TARGET_REQUIRED")
        target, info = self._target(acc, p["target"])
        ttype = info["type"]
        text = _str(p, "text", "AD_TEXT", lo=1, hi=160, req=ttype != "search")
        url = _str(p, "promote_url", "PROMOTE_URL", req=True, lo=1, hi=256)
        cpm = _float(p, "cpm", "CPM", req=True)
        assert cpm is not None and url is not None
        if cpm < MIN_CPM:
            raise ApiError("CPM_TOO_LOW")
        placement = _str(p, "placement", "PLACEMENT") or PLACEMENT_OF[ttype]
        if placement not in PLACEMENTS or (placement != "video_banner" and placement != PLACEMENT_OF[ttype]):
            raise ApiError("PLACEMENT_INVALID")
        freq = _int(p, "impression_frequency", "IMPRESSION_FREQUENCY", lo=1, hi=4)
        budget = _float(p, "initial_budget", "INITIAL_BUDGET", lo=0) or 0.0
        if 0 < budget < MIN_INITIAL_BUDGET:
            raise ApiError("BUDGET_TOO_LOW")
        daily = _float(p, "daily_budget_limit", "DAILY_BUDGET_LIMIT", lo=0)
        a: dict[str, Any] = {
            "title": title,
            "currency": "TON",
            "text": text or "",
            "promote_url": url,
            "cpm": cpm,
            "placement": placement,
            "target": target,
            "spent_budget": 0.0,
            "remaining_budget": 0.0,
            "views": 0,
            "clicks": 0,
            "actions": 0,
            "created_date": int(time.time()),
        }
        if freq:
            a["impression_frequency"] = freq
        if daily:
            a["daily_budget_limit"] = daily
        if _bool(p, "show_userpic"):
            a["show_userpic"] = True
        self._media(acc, a, p, ttype)
        self._website(acc, a, p, url, creating=True)
        self._dates(a, p)
        self._schedule(a, p, ttype)
        if budget > acc["account"]["remaining_budget"] + 1e-9:
            raise ApiError("NOT_ENOUGH_BUDGET")
        acc["next_id"] += 1
        a["ad_id"] = acc["next_id"]
        a["remaining_budget"] = round(budget, 5)
        acc["account"]["remaining_budget"] = round(acc["account"]["remaining_budget"] - budget, 5)
        acc["account"]["ads_budget"] = round(acc["account"].get("ads_budget", 0) + budget, 5)
        a["status"] = "in_review" if budget > 0 else "ready_for_review"
        acc["ads"][a["ad_id"]] = a
        acc["stats"].setdefault(a["ad_id"], [])
        if budget:
            self._ad_tx(acc, a["ad_id"], budget)
        return self._out_ad(a, p)

    def m_editAd(self, st: dict, p: dict, fmt: str = "json") -> dict:
        acc = self._acct(st, p)
        # A photo id is checked before the ad (the panel's capability probe relies on this order).
        if "photo_id" in p and self.supports_photo_edit and _given(p, "photo_id"):
            self._photo_ok(acc, str(p["photo_id"]), "photo")
        a = self._ad(acc, p)
        if self.ignores_edits or fmt in self.ignored_formats:
            # "ok" but the content is not stored: a real-API failure the panel must notice.
            content = {"title", "text", "photo_id", "video_id", "promote_url", "website_name", "website_photo_id",
                       "button", "additional_info"}  # fmt: skip
            p = {k: v for k, v in p.items() if k not in content}
        before = {k: json.dumps(a.get(k), sort_keys=True) for k in REVIEWED}
        new = dict(a)
        title = _str(p, "title", "AD_TITLE", lo=1, hi=128, nbytes=True)
        if title:
            new["title"] = title
        text = _str(p, "text", "AD_TEXT", lo=1, hi=160)
        if text is not None:
            new["text"] = text
        url = _str(p, "promote_url", "PROMOTE_URL", lo=1, hi=256)
        if url is not None:
            new["promote_url"] = url
        cpm = _float(p, "cpm", "CPM")
        if cpm is not None:
            if cpm < MIN_CPM:
                raise ApiError("CPM_TOO_LOW")
            new["cpm"] = cpm
        freq = _int(p, "impression_frequency", "IMPRESSION_FREQUENCY", lo=1, hi=4)
        if freq:
            new["impression_frequency"] = freq
        daily = _float(p, "daily_budget_limit", "DAILY_BUDGET_LIMIT", lo=0)
        if daily is not None:
            new["daily_budget_limit"] = daily
        if "show_userpic" in p:
            new["show_userpic"] = bool(_bool(p, "show_userpic"))
        ttype = (a.get("target") or {}).get("type", "channels")
        if self.supports_photo_edit:
            self._media(acc, new, p, ttype)
        self._website(acc, new, p, new["promote_url"], creating=False)
        was_paused = bool(a.get("is_paused"))
        self._dates(new, p)
        self._schedule(new, p, ttype)
        changed = [k for k in REVIEWED if json.dumps(new.get(k), sort_keys=True) != before[k]]
        if changed and self.edits_wait_for_review:
            waiting = acc.setdefault("pending", {}).setdefault(a["ad_id"], {})
            for k in changed:
                waiting[k] = new[k]
                new[k] = a.get(k)
        a.clear()
        a.update(new)
        if changed:
            self._review(a)
        if a.get("is_paused") and not was_paused:
            acc["paused_at"][a["ad_id"]] = time.time()
        elif was_paused and not a.get("is_paused"):
            acc["paused_at"].pop(a["ad_id"], None)
        return self._out_ad(a, p)

    def m_editAdStatus(self, st: dict, p: dict) -> dict:
        """Deprecated in favour of editAd(is_paused); kept working for old clients."""
        return self.m_editAd(st, {k: p[k] for k in ("account_id", "ad_id", "is_paused") if k in p})

    def m_increaseAdBudget(self, st: dict, p: dict) -> dict:
        acc = self._acct(st, p)
        a = self._ad(acc, p)
        amt = _float(p, "amount", "AMOUNT", req=True)
        if amt is None or amt <= 0:
            raise ApiError("AMOUNT_INVALID")
        if amt > acc["account"]["remaining_budget"] + 1e-9:
            raise ApiError("NOT_ENOUGH_BUDGET", executed=True)
        a["remaining_budget"] = round(a["remaining_budget"] + amt, 5)
        acc["account"]["remaining_budget"] = round(acc["account"]["remaining_budget"] - amt, 5)
        acc["account"]["ads_budget"] = round(acc["account"].get("ads_budget", 0) + amt, 5)
        self._ad_tx(acc, a["ad_id"], amt)
        return self._out_ad(a, p)

    def m_decreaseAdBudget(self, st: dict, p: dict) -> dict:
        acc = self._acct(st, p)
        a = self._ad(acc, p)
        amt = _float(p, "amount", "AMOUNT", req=True)
        if amt is None or amt <= 0:
            raise ApiError("AMOUNT_INVALID")
        if self._inactive_for(acc, a) < self.inactive_seconds or (a["status"] == "active" and not a.get("is_paused")):
            raise ApiError("AD_ACTIVE_RECENTLY", executed=True)
        if amt > a["remaining_budget"] + 1e-9:
            raise ApiError("NOT_ENOUGH_BUDGET", executed=True)
        a["remaining_budget"] = round(a["remaining_budget"] - amt, 5)
        acc["account"]["remaining_budget"] = round(acc["account"]["remaining_budget"] + amt, 5)
        acc["account"]["ads_budget"] = round(acc["account"].get("ads_budget", 0) - amt, 5)
        self._ad_tx(acc, a["ad_id"], -amt)
        return self._out_ad(a, p)

    def m_submitAdForReview(self, st: dict, p: dict) -> dict:
        acc = self._acct(st, p)
        a = self._ad(acc, p)
        if a["status"] != "ready_for_review":
            raise ApiError("AD_STATUS_INVALID")
        a["status"] = "in_review"
        return self._out_ad(a, p)

    def m_deleteAd(self, st: dict, p: dict) -> bool:
        acc = self._acct(st, p)
        a = self._ad(acc, p)
        if (a["status"] == "active" and not a.get("is_paused")) or self._inactive_for(acc, a) < self.inactive_seconds:
            raise ApiError("AD_ACTIVE_RECENTLY", executed=True)
        del acc["ads"][a["ad_id"]]
        acc.setdefault("deleted", {})[a["ad_id"]] = a["title"]
        return True

    def m_getAdsById(self, st: dict, p: dict) -> list:
        acc = self._acct(st, p)
        ids = _json(p, "ad_ids", "AD_IDS", list, req=True)
        return [self._out_ad(acc["ads"][int(i)], p) for i in ids if int(i) in acc["ads"]]

    def m_getAdsList(self, st: dict, p: dict) -> dict:
        acc = self._acct(st, p)
        items = sorted(acc["ads"].values(), key=lambda a: -a["ad_id"])
        if _given(p, "offset") and not str(p["offset"]).isdigit():
            raise ApiError("OFFSET_INVALID")
        chunk, nxt = _page(items, p, max_limit=100)
        out: dict[str, Any] = {"total_count": len(items), "ads": [self._out_ad(a, p) for a in chunk]}
        if nxt:
            out["next_offset"] = nxt
        return out

    def m_getAdTransactionsList(self, st: dict, p: dict) -> dict:
        acc = self._acct(st, p)
        a = self._ad(acc, p)
        txs = list(acc.get("ad_tx", {}).get(a["ad_id"], []))
        for r in acc["stats"].get(a["ad_id"], []):
            ts = calendar.timegm(time.strptime(r["date"], "%Y-%m-%d"))
            txs.append(
                {
                    "peer": {"type": "views", "views": r["views"]},
                    "date": ts,
                    "currency": "TON",
                    "amount": round(-r["spent"], 5),
                }
            )
        txs.sort(key=lambda t: -t["date"])
        chunk, nxt = _page(txs, p)
        out: dict[str, Any] = {"total_count": len(txs), "transactions": chunk}
        if nxt:
            out["next_offset"] = nxt
        return out

    def m_getAdStats(self, st: dict, p: dict) -> list:
        acc = self._acct(st, p)
        a = self._ad(acc, p)
        return self._stat_items(acc["stats"].get(a["ad_id"], []), p)

    # -------------------------------------------------------------- audiences
    def _aud_out(self, st: dict, aud: dict) -> dict:
        n = len(aud["phones"])
        used = sum(
            1
            for a in st["ads"].values()
            if aud["audience_id"]
            in ((a.get("target") or {}).get("audience_ids") or [])
            + ((a.get("target") or {}).get("exclude_audience_ids") or [])
        )
        return {
            "audience_id": aud["audience_id"],
            "title": aud["title"],
            "size": 0 if n == 0 else 50 if n < 100 else n,
            "ads_count": used,
            "created_date": aud["created_date"],
            "updated_date": aud["updated_date"],
        }

    def _phones(self, p: dict, k: str) -> set[str]:
        vals = _json(p, k, k.upper(), list) or []
        if len(vals) > 10000:
            raise ApiError(f"{k.upper()}_TOO_MANY")
        return {_phone_hash(v) for v in vals}

    def m_createAudience(self, st: dict, p: dict) -> dict:
        acc = self._acct(st, p)
        title = _str(p, "title", "AUDIENCE_TITLE", req=True, lo=1, hi=64)
        auds = acc.setdefault("audiences", {})
        aid = max(auds, default=0) + 1
        now = int(time.time())
        auds[aid] = {
            "audience_id": aid,
            "title": title,
            "phones": self._phones(p, "add_phones"),
            "created_date": now,
            "updated_date": now,
        }
        return self._aud_out(acc, auds[aid])

    def _aud(self, acc: dict, p: dict) -> dict:
        aid = _int(p, "audience_id", "AUDIENCE_ID", req=True)
        aud = acc.setdefault("audiences", {}).get(aid)
        if aud is None:
            raise ApiError("AUDIENCE_ID_INVALID")
        return aud

    def m_editAudience(self, st: dict, p: dict) -> dict:
        acc = self._acct(st, p)
        aud = self._aud(acc, p)
        title = _str(p, "title", "AUDIENCE_TITLE", lo=1, hi=64, nbytes=True)
        add = self._phones(p, "add_phones")
        if _bool(p, "reset_phones"):
            aud["phones"] = set()  # remove_phones is ignored when reset_phones is passed
        else:
            aud["phones"] -= self._phones(p, "remove_phones")
        aud["phones"] |= add  # deletions first, then additions
        if title:
            aud["title"] = title
        aud["updated_date"] = int(time.time())
        return self._aud_out(acc, aud)

    def m_deleteAudience(self, st: dict, p: dict) -> bool:
        acc = self._acct(st, p)
        aud = self._aud(acc, p)
        if self._aud_out(acc, aud)["ads_count"]:
            raise ApiError("AUDIENCE_IN_USE")
        del acc["audiences"][aud["audience_id"]]
        return True

    def m_getAudiencesById(self, st: dict, p: dict) -> list:
        acc = self._acct(st, p)
        ids = _json(p, "audience_ids", "AUDIENCE_IDS", list, req=True)
        auds = acc.setdefault("audiences", {})
        return [self._aud_out(acc, auds[int(i)]) for i in ids if int(i) in auds]

    def m_getAudiencesList(self, st: dict, p: dict) -> dict:
        acc = self._acct(st, p)
        auds = [
            self._aud_out(acc, a)
            for a in sorted(acc.setdefault("audiences", {}).values(), key=lambda a: a["audience_id"])
        ]
        chunk, _ = _page(auds, p, default=None)
        return {"total_count": len(auds), "audiences": chunk}

    # -------------------------------------------------------------- pixel
    def _event_out(self, acc: dict, ev: dict) -> dict:
        out = {k: v for k, v in ev.items() if v is not None}
        out["ads_count"] = sum(1 for a in acc["ads"].values() if a.get("conversion_event_id") == ev["event_id"])
        return out

    def m_createPixel(self, st: dict, p: dict) -> dict:
        acc = self._acct(st, p)
        if acc.get("pixel") is None:
            pid = "px_" + acc["account"]["account_id"]
            acc["pixel"] = {"pixel_id": pid, "code_snippet": f"<script>tgads('{pid}')</script>"}
            ev = {
                "event_id": "ev1",
                "title": "Page view",
                "type": "page_view",
                "status": "inactive",
                "created_date": int(time.time()),
                "auto_created": True,
            }
            acc.setdefault("pixel_events", {})["ev1"] = ev
        return acc["pixel"]

    def m_getPixel(self, st: dict, p: dict) -> dict:
        acc = self._acct(st, p)
        if acc.get("pixel") is None:
            raise ApiError("PIXEL_NOT_FOUND")
        return acc["pixel"]

    def m_createPixelEvent(self, st: dict, p: dict) -> dict:
        acc = self._acct(st, p)
        if acc.get("pixel") is None:
            raise ApiError("PIXEL_NOT_FOUND")
        title = _str(p, "title", "EVENT_TITLE", req=True, lo=1, hi=64)
        typ = _str(p, "type", "EVENT_TYPE", req=True)
        if typ not in PIXEL_EVENT_TYPES:
            raise ApiError("EVENT_TYPE_INVALID")
        events = acc.setdefault("pixel_events", {})
        eid = f"ev{len(events) + 1}"
        events[eid] = {
            "event_id": eid,
            "title": title,
            "type": typ,
            "status": "inactive",
            "created_date": int(time.time()),
            "code_snippet": f"<script>tgads.track('{eid}')</script>",
        }
        return self._event_out(acc, events[eid])

    def _event(self, acc: dict, p: dict) -> dict:
        eid = _str(p, "event_id", "EVENT_ID", req=True)
        ev = acc.get("pixel_events", {}).get(eid)
        if ev is None:
            raise ApiError("EVENT_ID_INVALID")
        return ev

    def m_editPixelEvent(self, st: dict, p: dict) -> dict:
        acc = self._acct(st, p)
        ev = self._event(acc, p)
        title = _str(p, "title", "EVENT_TITLE", req=True, lo=1, hi=64, nbytes=True)
        if ev.get("auto_created"):
            raise ApiError("EVENT_AUTO_CREATED")
        ev["title"] = title
        return self._event_out(acc, ev)

    def m_deletePixelEvent(self, st: dict, p: dict) -> bool:
        acc = self._acct(st, p)
        ev = self._event(acc, p)
        if ev.get("auto_created"):
            raise ApiError("EVENT_AUTO_CREATED")
        if self._event_out(acc, ev)["ads_count"]:
            raise ApiError("EVENT_IN_USE")
        del acc["pixel_events"][ev["event_id"]]
        return True

    def m_getPixelEventsById(self, st: dict, p: dict) -> list:
        acc = self._acct(st, p)
        ids = _json(p, "event_ids", "EVENT_IDS", list, req=True)
        events = acc.get("pixel_events", {})
        return [self._event_out(acc, events[str(i)]) for i in ids if str(i) in events]

    def m_getPixelEventsList(self, st: dict, p: dict) -> dict:
        acc = self._acct(st, p)
        events = [self._event_out(acc, e) for e in acc.get("pixel_events", {}).values()]
        chunk, _ = _page(events, p, default=None)
        return {"total_count": len(events), "events": chunk}
