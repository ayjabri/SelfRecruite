"""prompts, prompt_versions, job_messages, job_keywords: the per-job AI conversation

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-06 17:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
TS = postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        'prompts',
        sa.Column('name', sa.Text(), primary_key=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('updated_at', TS),
    )
    op.create_table(
        'prompt_versions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.Text(), sa.ForeignKey('prompts.name', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('updated_at', TS),
        sa.UniqueConstraint('name', 'version'),
    )
    op.create_table(
        'job_messages',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('job_id', sa.BigInteger(), sa.ForeignKey('jobs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('agent', sa.Text()),
        sa.Column('tool_calls', postgresql.JSONB()),
        sa.Column('model', sa.Text()),
        sa.Column('error', sa.Text()),
        sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('job_messages_thread_idx', 'job_messages', ['user_id', 'job_id', 'id'])
    op.create_table(
        'job_keywords',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('job_id', sa.BigInteger(), sa.ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('keywords', postgresql.JSONB(), nullable=False),
        sa.Column('summary', sa.Text()),
        sa.Column('context', postgresql.JSONB()),
        sa.Column('model', sa.Text()),
        sa.Column('prompt_version', sa.Integer()),
        sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('job_keywords')
    op.drop_index('job_messages_thread_idx', table_name='job_messages')
    op.drop_table('job_messages')
    op.drop_table('prompt_versions')
    op.drop_table('prompts')
