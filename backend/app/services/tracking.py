"""Per-ad sales tracking: how each ad's link (`?start=`) is chosen in a bulk referral operation.

The sales bot attributes a new user to a source when the `start` parameter is the numeric id of a user
that already exists in the bot (its referral system, read back with `users → affiliates_users`).

* "pool"    referrer accounts the advertiser owns; each ad (or group of ads) gets a free one.
* "shared"  one referrer (or existing tag) for every selected ad: sales are seen for the group only.
* "random"  a unique random numeric code per ad, each with its own tag. It is checked not to be the id
            of a real bot user. Whether the bot records users who arrive with an unknown code is not
            documented, so the panel runs a "random code tracking" test (see `start_test`/`check_test`):
            works   the bot lists those users under the code in `affiliates_users`: sales per ad work;
            ignored a new user arrived but is not listed: links work, sales are not attributed;
            unknown nobody has started the test link yet.
Real customers' ids are never used as referrers (they could be paid commission and get messages).
"""

import secrets
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.base import now_ts
from app.integrations.bot_api import BotApiClient, BotApiError
from app.models import Account, ReferralTag
from app.services import accounts, events
from app.services.accounts import settings_of

# Codes are 10 digits from 9,900,000,000 up: above the range Telegram has handed out to users so far, so a
# code is very unlikely to be (or soon become) a real person's id. Each one is still checked in the bot.
CODE_MIN = 9_900_000_000
CODE_SPAN = 99_999_999


@dataclass(frozen=True)
class TrackingMode:
    key: str
    label: str
    description: str

    def public(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "description": self.description}


POOL = TrackingMode(
    "pool",
    "از استخر معرف‌ها",
    "هر آگهی (یا گروه آگهی) لینک دعوت یکی از حساب‌های معرفِ خودتان را می‌گیرد.",
)
SHARED = TrackingMode(
    "shared",
    "یک لینک مشترک برای همه",
    "همه‌ی آگهی‌های انتخاب‌شده لینک یک معرف یا برچسب را می‌گیرند؛ فروش فقط برای کل گروه دیده می‌شود.",
)
RANDOM = TrackingMode(
    "random",
    "لینک رندوم اختصاصی برای هر آگهی",
    "برای هر آگهی یک کد عددی تصادفی و یکتا ساخته می‌شود (مثل 9934521877) با برچسب خودش.",
)
MODES = (POOL, SHARED, RANDOM)

CAMPAIGN_REQUEST = """سلام. برای ردیابی فروش هر آگهی تلگرام، لطفاً این را به ربات و API آن اضافه کنید:
۱) وقتی کاربر جدید با لینک t.me/<ربات>?start=<کد> وارد می‌شود و <کد> عددی است ولی شناسه‌ی هیچ کاربری نیست (مثل 9934521877)، همان کد را روی کاربر ذخیره کنید (مثلاً ستون start_code یا به‌عنوان معرف).
۲) اکشن users → affiliates_users با chat_id=<کد> کاربرانی را که با آن کد آمده‌اند برگرداند (مثل معرف‌های واقعی)، یا در users → user فیلد start_code را برگردانید.
۳) به این کاربران پورسانت یا پیام دعوت تعلق نگیرد، چون کد متعلق به هیچ کاربری نیست.
ممنون."""


def random_code() -> str:
    return str(CODE_MIN + secrets.randbelow(CODE_SPAN))


async def used_codes(s: AsyncSession, acc: Account) -> set[str]:
    rows = await s.execute(
        select(ReferralTag.start_code, ReferralTag.referrer_id).where(ReferralTag.account_id == acc.id)
    )
    out: set[str] = set()
    for code, rid in rows:
        out.update((str(code), str(rid)))
    return out


async def _is_bot_user(c: BotApiClient, code: str) -> bool:
    """True when `code` is an existing user of the bot (then it cannot be a tracking code)."""
    try:
        obj = await c.user(code)
    except BotApiError as e:
        if e.http_status is not None and e.http_status < 500 and not e.forbidden:
            return False  # e.g. {"status": false, "msg": "user not found"}
        raise
    return bool(obj)


async def new_codes(s: AsyncSession, acc: Account, n: int) -> list[str]:
    """`n` unique random codes, none of them a tag code already or the id of a bot user."""
    taken = await used_codes(s, acc)
    out: list[str] = []
    try:
        async with accounts.bot_client(acc) as c:
            tries = 0
            while len(out) < n:
                tries += 1
                if tries > n * 5 + 20:
                    raise AppError("ساخت کد یکتا ممکن نشد؛ دوباره امتحان کنید.")
                code = random_code()
                if code in taken or code in out:
                    continue
                if await _is_bot_user(c, code):
                    taken.add(code)
                    continue
                out.append(code)
    except BotApiError as e:
        raise AppError(f"بررسی کدها در ربات انجام نشد: {e.friendly}") from None
    return out


async def ensure_random_tag(s: AsyncSession, acc: Account, code: str, label: str) -> ReferralTag:
    tag = ReferralTag(
        account_id=acc.id,
        name=f"رندوم {label}"[:80],
        referrer_id=code,
        start_code=code,
        kind="random",
        note="کد رندوم اختصاصی این آگهی (عملیات گروهی)",
        created_ts=now_ts(),
    )
    s.add(tag)
    await s.flush()
    return tag


# ---------------------------------------------------------------- the random code tracking test


def status(acc: Account) -> dict[str, Any]:
    st = settings_of(acc)
    test = (acc.sync_state or {}).get("random_test") or None
    return {"result": st["random_tracking"], "test": test}


async def start_test(s: AsyncSession, acc: Account) -> dict[str, Any]:
    """Make a test code and remember the bot's user count, then ask the user to start the bot with it."""
    if not acc.bot_username:
        raise AppError("نام کاربری ربات در اتصال ربات وارد نشده است.")
    code = (await new_codes(s, acc, 1))[0]
    try:
        async with accounts.bot_client(acc) as c:
            stats = await c.statbot()
    except BotApiError as e:
        raise AppError(f"آمار ربات خوانده نشد: {e.friendly}") from None
    test = {
        "code": code,
        "link": f"https://t.me/{acc.bot_username}?start={code}",
        "created_ts": now_ts(),
        "users_before": stats.get("count_user"),
        "result": None,
        "checked_ts": 0,
    }
    state = dict(acc.sync_state or {})
    state["random_test"] = test
    acc.sync_state = state
    events.log(s, "random_test_started", account_id=acc.id, actor="user", detail=code)
    return test


async def check_test(s: AsyncSession, acc: Account) -> dict[str, Any]:
    """works: users listed under the code; ignored: the bot got a new user but did not list it; unknown."""
    state = dict(acc.sync_state or {})
    test = dict(state.get("random_test") or {})
    if not test.get("code"):
        raise AppError("اول آزمایش را شروع کنید.")
    try:
        async with accounts.bot_client(acc) as c:
            rows = await c.affiliates(test["code"], page=1, limit=100)
            stats = await c.statbot()
    except BotApiError as e:
        raise AppError(f"ربات جواب نداد: {e.friendly}") from None
    before = test.get("users_before")
    after = stats.get("count_user")
    if rows:
        result = "works"
    elif isinstance(before, int) and isinstance(after, int) and after > before:
        result = "ignored"
    else:
        result = "unknown"
    test.update(result=result, checked_ts=now_ts(), users_after=after, listed=len(rows))
    state["random_test"] = test
    acc.sync_state = state
    if result != "unknown" or settings_of(acc)["random_tracking"] == "unknown":
        acc.settings = {**(acc.settings or {}), "random_tracking": result}
    events.log(s, "random_test_checked", account_id=acc.id, actor="user", detail=result)
    return test
