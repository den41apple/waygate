"""server_host_index

Revision ID: c8d2a4f1e9b3
Revises: f3a91d4c2e8b
Create Date: 2026-05-05 11:00:00.000000+00:00

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c8d2a4f1e9b3"
down_revision: str | None = "f3a91d4c2e8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Индекс на `server.host` — provision делает upsert по host'у на каждое
    onboarding'е. Full-scan на тысячах серверов = заметный jitter."""
    op.create_index("ix_server_host", "server", ["host"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_server_host", table_name="server")
