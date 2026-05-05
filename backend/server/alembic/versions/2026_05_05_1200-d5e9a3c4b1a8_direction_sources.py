"""direction_sources pivot table

Revision ID: d5e9a3c4b1a8
Revises: c8d2a4f1e9b3
Create Date: 2026-05-05 12:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "d5e9a3c4b1a8"
down_revision: str | None = "c8d2a4f1e9b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создаёт `direction_sources` pivot + back-fill из существующих RoutingRule.

    До этого _collect_refs вычислял geo/dns/ipset_group ids reverse-lookup'ом
    по `RoutingRule.ipset_name` (string-matching `geoip-ru-v4` → GeoList(country=ru)).
    Это работало пока имена были предсказуемые, но плохо масштабируется на
    новые типы источников (ASN list и т.п.). Теперь источники хранятся явно.
    """
    op.create_table(
        "direction_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "direction_id",
            sa.Integer(),
            sa.ForeignKey("routing_directions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("source_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "direction_id",
            "source_type",
            "source_id",
            name="uq_direction_source",
        ),
    )

    # Data-migration: для каждого существующего direction'а с child-rule'ами
    # восстанавливаем источники тем же reverse-lookup'ом, что был в _collect_refs.
    # Frozen-snapshot принцип — не импортируем ORM-модели, работаем сырым SQL.
    bind = op.get_bind()

    # Соберём все direction'ы и их child-RoutingRule.ipset_name'ы.
    rows = bind.execute(
        sa.text(
            "SELECT rr.direction_id, rr.server_id, rr.ipset_name "
            "FROM routing_rule rr "
            "WHERE rr.direction_id IS NOT NULL AND rr.ipset_name != ''",
        ),
    ).fetchall()

    # Pre-load lookups: GeoList по country (для всех row'ов сразу).
    geo_rows = bind.execute(sa.text("SELECT id, LOWER(country) AS country FROM geo_list")).fetchall()
    geo_by_country: dict[str, int] = {row.country: row.id for row in geo_rows}

    dns_rows = bind.execute(sa.text("SELECT id, server_id, ipset_name FROM dns_rule")).fetchall()
    dns_by_key: dict[tuple[int, str], int] = {(row.server_id, row.ipset_name): row.id for row in dns_rows}

    group_rows = bind.execute(sa.text("SELECT id, server_id, name FROM ipset_groups")).fetchall()
    group_by_key: dict[tuple[int, str], int] = {(row.server_id, row.name): row.id for row in group_rows}

    inserted: set[tuple[int, str, int]] = set()

    for row in rows:
        direction_id = row.direction_id
        server_id = row.server_id
        ipset_name = row.ipset_name

        # geoip-<cc> или legacy geoip-<cc>-v4
        if ipset_name.startswith("geoip-"):
            country = ipset_name.removeprefix("geoip-").removesuffix("-v4").removesuffix("-v6")
            geo_id = geo_by_country.get(country.lower())
            if geo_id is not None:
                key = (direction_id, "geo_list", geo_id)
                if key not in inserted:
                    inserted.add(key)
                    bind.execute(
                        sa.text(
                            "INSERT INTO direction_sources (direction_id, source_type, source_id) "
                            "VALUES (:d, 'geo_list', :s)",
                        ),
                        {"d": direction_id, "s": geo_id},
                    )
            continue

        # DNS-rule по (server_id, ipset_name)
        dns_id = dns_by_key.get((server_id, ipset_name))
        if dns_id is not None:
            key = (direction_id, "dns_rule", dns_id)
            if key not in inserted:
                inserted.add(key)
                bind.execute(
                    sa.text(
                        "INSERT INTO direction_sources (direction_id, source_type, source_id) "
                        "VALUES (:d, 'dns_rule', :s)",
                    ),
                    {"d": direction_id, "s": dns_id},
                )
            continue

        # IpsetGroup по (server_id, name)
        group_id = group_by_key.get((server_id, ipset_name))
        if group_id is not None:
            key = (direction_id, "ipset_group", group_id)
            if key not in inserted:
                inserted.add(key)
                bind.execute(
                    sa.text(
                        "INSERT INTO direction_sources (direction_id, source_type, source_id) "
                        "VALUES (:d, 'ipset_group', :s)",
                    ),
                    {"d": direction_id, "s": group_id},
                )


def downgrade() -> None:
    op.drop_table("direction_sources")
