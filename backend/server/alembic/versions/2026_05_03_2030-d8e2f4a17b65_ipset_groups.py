"""ipset_groups

Revision ID: d8e2f4a17b65
Revises: c41a8b5d92e7
Create Date: 2026-05-03 20:30:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "d8e2f4a17b65"
down_revision: str | None = "c41a8b5d92e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ipset_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("cidrs", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["server_id"], ["server.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("server_id", "name", name="uq_ipset_group_server_name"),
    )
    with op.batch_alter_table("ipset_groups", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ipset_groups_server_id"), ["server_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("ipset_groups", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ipset_groups_server_id"))
    op.drop_table("ipset_groups")
