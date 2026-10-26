from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Ad(Base):
    __tablename__ = "ads"
    __table_args__ = (UniqueConstraint("account_id", "ad_id", name="uq_ads_account_ad"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    ad_id: Mapped[int] = mapped_column(BigInteger)  # Telegram's id, unique per account
    title: Mapped[str] = mapped_column(String(200), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    promote_url: Mapped[str] = mapped_column(String(300), default="")
    start_code: Mapped[str] = mapped_column(String(128), default="")
    cpm: Mapped[float] = mapped_column(Float, default=0)
    placement: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(32), default="")
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    views: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    actions: Mapped[int] = mapped_column(Integer, default=0)
    action_type: Mapped[str] = mapped_column(String(32), default="")
    spent_budget: Mapped[float] = mapped_column(Float, default=0)
    remaining_budget: Mapped[float] = mapped_column(Float, default=0)
    daily_budget_limit: Mapped[float] = mapped_column(Float, default=0)
    created_date: Mapped[int] = mapped_column(Integer, default=0)
    target_type: Mapped[str] = mapped_column(String(16), default="")
    target_label: Mapped[str] = mapped_column(Text, default="")
    target: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    has_photo: Mapped[bool] = mapped_column(Boolean, default=False)
    has_video: Mapped[bool] = mapped_column(Boolean, default=False)
    decline_reason: Mapped[str] = mapped_column(Text, default="")
    gone: Mapped[bool] = mapped_column(Boolean, default=False)  # deleted on Telegram
    synced_ts: Mapped[int] = mapped_column(Integer, default=0)
    stats_ts: Mapped[int] = mapped_column(Integer, default=0)
    stats_spent: Mapped[float] = mapped_column(Float, default=-1)  # spent_budget at the last stats fetch

    # Our own annotations
    tag_id: Mapped[int | None] = mapped_column(ForeignKey("referral_tags.id", ondelete="SET NULL"))
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("text_variants.id", ondelete="SET NULL"))
    note: Mapped[str] = mapped_column(Text, default="")
    created_by_app: Mapped[bool] = mapped_column(Boolean, default=False)

    # Budget history (see services/budget.py): start of the current funded stretch, or since when it is empty.
    funded_since_ts: Mapped[int] = mapped_column(Integer, default=0)
    empty_since_ts: Mapped[int] = mapped_column(Integer, default=0)
    # Retired by a bulk replacement: stopped, no budget, hidden from the default lists.
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    archived_ts: Mapped[int] = mapped_column(Integer, default=0)


class AdStat(Base):
    __tablename__ = "ad_stats"
    __table_args__ = (UniqueConstraint("account_id", "ad_id", "day", name="uq_ad_stats_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    ad_id: Mapped[int] = mapped_column(BigInteger, index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD (UTC)
    views: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    actions: Mapped[int] = mapped_column(Integer, default=0)
    spent: Mapped[float] = mapped_column(Float, default=0)


class AdBudgetEvent(Base):
    """A change of an ad's remaining budget: funded (from empty), empty, topup or withdraw.

    `actor` is "app" for changes the panel made, "site" for jumps nobody here caused (a top-up on
    ads.telegram.org) and "sync" for transitions observed by the periodic sync (spent down to zero).
    """

    __tablename__ = "ad_budget_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    ad_id: Mapped[int] = mapped_column(BigInteger, index=True)
    ts: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # funded | empty | topup | withdraw
    amount: Mapped[float] = mapped_column(Float, default=0)
    remaining: Mapped[float] = mapped_column(Float, default=0)  # remaining budget after the change
    actor: Mapped[str] = mapped_column(String(16), default="sync")


class AdDayBaseline(Base):
    """An ad's lifetime counters at the start of a local day (the account's bot time zone).

    Telegram's daily statistics are per UTC day; "today" in the panel starts at local midnight, so it is
    the current counters minus this baseline, recorded by the first fast sync of the day.
    """

    __tablename__ = "ad_day_baselines"
    __table_args__ = (UniqueConstraint("account_id", "ad_id", "day", name="uq_ad_day_baselines_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    ad_id: Mapped[int] = mapped_column(BigInteger, index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD in the account's time zone
    ts: Mapped[int] = mapped_column(Integer, default=0)
    views: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    actions: Mapped[int] = mapped_column(Integer, default=0)
    spent: Mapped[float] = mapped_column(Float, default=0)
