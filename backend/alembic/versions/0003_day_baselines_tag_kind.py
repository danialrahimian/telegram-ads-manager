"""per-ad day baselines, referral tag kind, settings renamed in 2.2

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-26 09:00:00
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Removed from the account settings in 2.2: replacements now start at min_initial_budget (Telegram's
# minimum, 1 TON by default), and the tracking choice moved into the bulk referral dialog.
DROPPED_SETTINGS = ("bulk_min_budget", "tracking_mode")


def upgrade() -> None:
    op.create_table(
        "ad_day_baselines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("ad_id", sa.BigInteger(), nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("ts", sa.Integer(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False),
        sa.Column("clicks", sa.Integer(), nullable=False),
        sa.Column("actions", sa.Integer(), nullable=False),
        sa.Column("spent", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_ad_day_baselines_account_id_accounts"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ad_day_baselines")),
        sa.UniqueConstraint("account_id", "ad_id", "day", name="uq_ad_day_baselines_day"),
    )
    with op.batch_alter_table("ad_day_baselines", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ad_day_baselines_account_id"), ["account_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_ad_day_baselines_ad_id"), ["ad_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_ad_day_baselines_day"), ["day"], unique=False)

    with op.batch_alter_table("referral_tags", schema=None) as batch_op:
        batch_op.add_column(sa.Column("kind", sa.String(length=16), nullable=False, server_default="referrer"))

    conn = op.get_bind()
    for acc_id, raw in conn.execute(sa.text("SELECT id, settings FROM accounts")).fetchall():
        try:
            settings = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except (TypeError, ValueError):
            continue
        if not isinstance(settings, dict) or not any(k in settings for k in DROPPED_SETTINGS):
            continue
        for k in DROPPED_SETTINGS:
            settings.pop(k, None)
        conn.execute(
            sa.text("UPDATE accounts SET settings = :s WHERE id = :i"), {"s": json.dumps(settings), "i": acc_id}
        )


def downgrade() -> None:
    with op.batch_alter_table("referral_tags", schema=None) as batch_op:
        batch_op.drop_column("kind")
    op.drop_table("ad_day_baselines")
