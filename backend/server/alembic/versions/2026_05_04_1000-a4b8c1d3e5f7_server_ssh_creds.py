"""server_ssh_creds

Revision ID: a4b8c1d3e5f7
Revises: e92c5b1f3a87
Create Date: 2026-05-04 10:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "a4b8c1d3e5f7"
down_revision: str | None = "e92c5b1f3a87"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("server", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "ssh_user",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=False,
                server_default="root",
            ),
        )
        batch_op.add_column(
            sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="22"),
        )
        batch_op.add_column(
            sa.Column("ssh_password_encrypted", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("ssh_private_key_encrypted", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("server", schema=None) as batch_op:
        batch_op.drop_column("ssh_private_key_encrypted")
        batch_op.drop_column("ssh_password_encrypted")
        batch_op.drop_column("ssh_port")
        batch_op.drop_column("ssh_user")
