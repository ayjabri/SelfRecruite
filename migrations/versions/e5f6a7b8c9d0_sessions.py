"""sessions: signed-in browsers

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-06 14:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
TS = postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        'sessions',
        sa.Column('token_hash', sa.Text(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('csrf', sa.Text(), nullable=False),
        sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', TS, nullable=False),
        sa.Column('last_seen_at', TS, server_default=sa.text('now()'), nullable=False),
        sa.Column('user_agent', sa.Text()),
    )


def downgrade() -> None:
    op.drop_table('sessions')
