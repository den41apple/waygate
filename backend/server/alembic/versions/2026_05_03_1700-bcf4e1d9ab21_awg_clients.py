"""awg_clients

Revision ID: bcf4e1d9ab21
Revises: 544044e3a946
Create Date: 2026-05-03 17:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "bcf4e1d9ab21"
down_revision: str | None = "544044e3a946"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "awg_clients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("container_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("config_encrypted", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("peer_endpoint", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("peer_pubkey", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("interface_address", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("country", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["server_id"], ["server.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("server_id", "name", name="uq_awg_client_server_name"),
    )
    with op.batch_alter_table("awg_clients", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_awg_clients_server_id"), ["server_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("awg_clients", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_awg_clients_server_id"))
    op.drop_table("awg_clients")
