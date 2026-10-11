"""score check: the score a job had when the user marked it; held-out scores and training history

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-11 09:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user_jobs', sa.Column('label_score', sa.Integer()))
    op.add_column('preference_models', sa.Column('diagnostics', postgresql.JSONB(), server_default='{}', nullable=False))


def downgrade() -> None:
    op.drop_column('preference_models', 'diagnostics')
    op.drop_column('user_jobs', 'label_score')
