"""default_egress (catch-all direction)

Revision ID: f3a91d4c2e8b
Revises: a4b8c1d3e5f7
Create Date: 2026-05-05 10:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a91d4c2e8b"
down_revision: str | None = "a4b8c1d3e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Добавляет `is_default_egress: bool` к routing_directions и routing_rule.

    Также — частичный UNIQUE-индекс на (server_id, scope) для default-egress
    direction'ов: на сервер max один catch-all per scope, но обычные direction'ы
    по-прежнему могут дублироваться по scope. Postgres понимает `WHERE`-clause
    в UNIQUE-индексе, SQLite (тестовая среда) — тоже.
    """
    with op.batch_alter_table("routing_directions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_default_egress",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    with op.batch_alter_table("routing_rule", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_default_egress",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    # Частичный UNIQUE: один catch-all на (server_id, scope). Обычные направления
    # без is_default_egress=True вообще не индексируются этим constraint'ом.
    op.create_index(
        "uq_default_egress_per_scope",
        "routing_directions",
        ["server_id", "scope"],
        unique=True,
        postgresql_where=sa.text("is_default_egress = TRUE"),
        sqlite_where=sa.text("is_default_egress = TRUE"),
    )


def downgrade() -> None:
    op.drop_index("uq_default_egress_per_scope", table_name="routing_directions")
    with op.batch_alter_table("routing_rule", schema=None) as batch_op:
        batch_op.drop_column("is_default_egress")
    with op.batch_alter_table("routing_directions", schema=None) as batch_op:
        batch_op.drop_column("is_default_egress")
