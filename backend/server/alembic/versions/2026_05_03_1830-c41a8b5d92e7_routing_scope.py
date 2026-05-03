"""routing_scope

Revision ID: c41a8b5d92e7
Revises: bcf4e1d9ab21
Create Date: 2026-05-03 18:30:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "c41a8b5d92e7"
down_revision: str | None = "bcf4e1d9ab21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("routing_rule", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "scope",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=False,
                server_default="host",
            ),
        )
        batch_op.add_column(
            sa.Column("scope_target", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("routing_rule", schema=None) as batch_op:
        batch_op.drop_column("scope_target")
        batch_op.drop_column("scope")
