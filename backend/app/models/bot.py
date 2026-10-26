from sqlalchemy import BigInteger, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReferralTag(Base):
    """A referrer account inside the sales bot whose invite link is used as an ad's link.

    Every user who starts the bot through that link gets this referrer, so their purchases
    can be attributed to the ads that use the tag.
    """

    __tablename__ = "referral_tags"
    __table_args__ = (UniqueConstraint("account_id", "start_code", name="uq_referral_tags_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    referrer_id: Mapped[str] = mapped_column(String(32))  # Telegram numeric id of the referrer account
    start_code: Mapped[str] = mapped_column(String(128))  # value after ?start= in the invite link
    # "referrer": a real referrer account in the bot; "random": a random numeric code made for one ad
    kind: Mapped[str] = mapped_column(String(16), default="referrer")
    note: Mapped[str] = mapped_column(Text, default="")
    referred_count: Mapped[int] = mapped_column(Integer, default=0)
    last_synced_ts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_ts: Mapped[int] = mapped_column(Integer, default=0)


class BotUser(Base):
    """A bot user that came through one of our referral tags."""

    __tablename__ = "bot_users"
    __table_args__ = (UniqueConstraint("account_id", "user_id", name="uq_bot_users_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    tag_id: Mapped[int | None] = mapped_column(ForeignKey("referral_tags.id", ondelete="SET NULL"), index=True)
    registered_ts: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_ts: Mapped[int] = mapped_column(Integer, default=0)


class BotTransaction(Base):
    """One row of the bot's wallet ledger (purchases, renewals, top-ups, refunds...)."""

    __tablename__ = "bot_transactions"
    __table_args__ = (UniqueConstraint("account_id", "ext_id", name="uq_bot_tx_ext"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    ext_id: Mapped[str] = mapped_column(String(80))
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, default=0)
    type: Mapped[str] = mapped_column(String(40), default="", index=True)
    source: Mapped[str] = mapped_column(String(40), default="")
    amount: Mapped[float] = mapped_column(Float, default=0)
    created_ts: Mapped[int] = mapped_column(Integer, index=True, default=0)
