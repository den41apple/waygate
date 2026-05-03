"""routing_directions

Revision ID: e92c5b1f3a87
Revises: d8e2f4a17b65
Create Date: 2026-05-03 21:00:00.000000+00:00

"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "e92c5b1f3a87"
down_revision: str | None = "d8e2f4a17b65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Новая таблица — header'ы направлений.
    op.create_table(
        "routing_directions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("awg_client_id", sa.Integer(), nullable=True),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("fwmark", sa.Integer(), nullable=False),
        sa.Column("table_id", sa.Integer(), nullable=False),
        sa.Column("via_interface", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("via_gateway", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("scope", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="host"),
        sa.Column("scope_target", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["server_id"], ["server.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["awg_client_id"], ["awg_clients.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("server_id", "name", name="uq_direction_server_name"),
    )
    with op.batch_alter_table("routing_directions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_routing_directions_server_id"),
            ["server_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_routing_directions_awg_client_id"),
            ["awg_client_id"],
            unique=False,
        )

    # 2. RoutingRule.direction_id — связка многие-к-одному с RoutingDirection.
    with op.batch_alter_table("routing_rule", schema=None) as batch_op:
        batch_op.add_column(sa.Column("direction_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_routing_rule_direction",
            "routing_directions",
            ["direction_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index(
            batch_op.f("ix_routing_rule_direction_id"),
            ["direction_id"],
            unique=False,
        )

    # 3. Data-migration: legacy RoutingRule (direction_id IS NULL) → synthetic
    #    RoutingDirection. Группируем по совпадающим параметрам туннеля; в каждой
    #    группе одно направление с именем `legacy-<via_interface>` (с
    #    автоинкрементом при коллизии). Логика дублирует то, что раньше
    #    жило в server/scripts/migrate_legacy_rules.py — теперь применяется
    #    автоматически на `alembic upgrade head`.
    _migrate_legacy_rules()


def downgrade() -> None:
    with op.batch_alter_table("routing_rule", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_routing_rule_direction_id"))
        batch_op.drop_constraint("fk_routing_rule_direction", type_="foreignkey")
        batch_op.drop_column("direction_id")

    with op.batch_alter_table("routing_directions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_routing_directions_awg_client_id"))
        batch_op.drop_index(batch_op.f("ix_routing_directions_server_id"))
    op.drop_table("routing_directions")


def _migrate_legacy_rules() -> None:
    """Перекладывает legacy RoutingRule в новые RoutingDirection."""
    bind = op.get_bind()

    groups = bind.execute(
        sa.text("""
            SELECT server_id, via_interface, via_gateway, fwmark, table_id, scope, scope_target,
                   MAX(CASE WHEN enabled THEN 1 ELSE 0 END) AS any_enabled
            FROM routing_rule
            WHERE direction_id IS NULL
            GROUP BY server_id, via_interface, via_gateway, fwmark, table_id, scope, scope_target
        """),
    ).fetchall()

    if not groups:
        return

    now = datetime.now()
    for group in groups:
        # Подбор уникального имени: legacy-<iface>, legacy-<iface>-2, …
        base_name = f"legacy-{group.via_interface}"
        name = base_name
        suffix = 2
        while (
            bind.execute(
                sa.text(
                    "SELECT 1 FROM routing_directions WHERE server_id=:sid AND name=:n",
                ),
                {"sid": group.server_id, "n": name},
            ).first()
            is not None
        ):
            name = f"{base_name}-{suffix}"
            suffix += 1

        bind.execute(
            sa.text("""
                INSERT INTO routing_directions
                    (server_id, awg_client_id, name, fwmark, table_id, via_interface,
                     via_gateway, scope, scope_target, enabled, created_at, updated_at)
                VALUES
                    (:sid, NULL, :name, :fw, :tab, :iface, :gw, :scope, :st, :enabled, :now, :now)
            """),
            {
                "sid": group.server_id,
                "name": name,
                "fw": group.fwmark,
                "tab": group.table_id,
                "iface": group.via_interface,
                "gw": group.via_gateway,
                "scope": group.scope,
                "st": group.scope_target,
                "enabled": bool(group.any_enabled),
                "now": now,
            },
        )

        # SQLite < 3.35 не умеет RETURNING — отдельный SELECT по UNIQUE-имени.
        direction_id_row = bind.execute(
            sa.text("SELECT id FROM routing_directions WHERE server_id=:sid AND name=:n"),
            {"sid": group.server_id, "n": name},
        ).first()
        assert direction_id_row is not None
        direction_id = direction_id_row.id

        # scope_target NULLable — IS NULL и `=` ведут себя по-разному.
        bind.execute(
            sa.text("""
                UPDATE routing_rule
                   SET direction_id=:did
                 WHERE direction_id IS NULL
                   AND server_id=:sid
                   AND via_interface=:iface
                   AND via_gateway=:gw
                   AND fwmark=:fw
                   AND table_id=:tab
                   AND scope=:scope
                   AND ((:st IS NULL AND scope_target IS NULL) OR scope_target=:st)
            """),
            {
                "did": direction_id,
                "sid": group.server_id,
                "iface": group.via_interface,
                "gw": group.via_gateway,
                "fw": group.fwmark,
                "tab": group.table_id,
                "scope": group.scope,
                "st": group.scope_target,
            },
        )
