"""user_jobs.applied_resume_id: which resume went out with an application

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-06 16:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user_jobs', sa.Column('applied_resume_id', sa.Integer(),
                                         sa.ForeignKey('resumes.id', ondelete='SET NULL'), nullable=True))


def downgrade() -> None:
    op.drop_column('user_jobs', 'applied_resume_id')
