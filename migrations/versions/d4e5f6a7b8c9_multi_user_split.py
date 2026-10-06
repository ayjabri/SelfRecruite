"""multi-user split: users, profiles, resumes, user_jobs, preference_models

Moves every per-user column off jobs into user_jobs, owned by user 1, who is created from
the current config files, resume and preference model. Nothing is lost.

Revision ID: d4e5f6a7b8c9
Revises: c27c4780e9e6
Create Date: 2026-10-06 10:30:00
"""
import hashlib
import json
import os
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c27c4780e9e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PER_USER = ["fit_score", "fit_details", "user_label", "user_labeled_at", "favorite", "applied_at", "notes",
            "match_score", "match_explanation", "match_details", "match_model", "golden_source_sha", "scored_at"]
TS = postgresql.TIMESTAMP(timezone=True)


def _seed_profile() -> dict:
    """The current config files become user 1's profile."""
    import yaml
    from jobagent.config import ROOT, get_settings
    s = get_settings()
    load = lambda p: (yaml.safe_load(Path(p).read_text()) if Path(p).exists() else None) or {}  # noqa: E731
    filters, targets, skills = load(s.filters_file), load(s.targets_file), load(ROOT / "config" / "skills.yaml")
    return {
        "targets": {k: v for k, v in targets.items() if k != "bank_title_companies"},
        "scope": {"title": filters.get("title") or {},
                  "location": {"allowed_states": (filters.get("location") or {}).get("allowed_states") or [],
                               "remote_ok": True}},
        "skills": {"have": skills.get("have") or [], "not_have": skills.get("not_have") or []},
        "llm": {"judge_budget_per_day": 20},
    }


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('email', sa.Text(), nullable=False, unique=True),
        sa.Column('name', sa.Text()),
        sa.Column('password_hash', sa.Text()),
        sa.Column('role', sa.Text(), server_default='member', nullable=False),
        sa.Column('status', sa.Text(), server_default='active', nullable=False),
        sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
    )
    op.create_table(
        'profiles',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('targets', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('scope', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('skills', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('llm', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('updated_at', TS, server_default=sa.text('now()'), nullable=False),
    )
    op.create_table(
        'resumes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('filename', sa.Text()),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('sha', sa.Text(), nullable=False),
        sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('uploaded_at', TS, server_default=sa.text('now()'), nullable=False),
    )
    op.create_table(
        'user_jobs',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('job_id', sa.BigInteger(), sa.ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('in_scope', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('fit_score', sa.SmallInteger()),
        sa.Column('fit_details', postgresql.JSONB()),
        sa.Column('user_label', sa.Text()),
        sa.Column('user_labeled_at', TS),
        sa.Column('favorite', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('applied_at', TS),
        sa.Column('notes', sa.Text()),
        sa.Column('match_score', sa.SmallInteger()),
        sa.Column('match_explanation', sa.Text()),
        sa.Column('match_details', postgresql.JSONB()),
        sa.Column('match_model', sa.Text()),
        sa.Column('golden_source_sha', sa.Text()),
        sa.Column('scored_at', TS),
        sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('user_jobs_score_idx', 'user_jobs', ['user_id', 'fit_score'])
    op.create_index('user_jobs_label_idx', 'user_jobs', ['user_id', 'user_label'])
    op.create_table(
        'preference_models',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('trained_at', TS, server_default=sa.text('now()'), nullable=False),
        sa.Column('meta', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('blob', sa.LargeBinary(), nullable=False),
    )
    op.add_column('runs', sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')))
    op.add_column('golden_chunks', sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE')))
    op.add_column('golden_chunks', sa.Column('resume_id', sa.Integer(), sa.ForeignKey('resumes.id', ondelete='CASCADE')))

    # --- data: everything so far belongs to user 1 -------------------------------------------
    conn = op.get_bind()
    email = os.environ.get("ADMIN_EMAIL", "admin@localhost").lower()
    conn.execute(sa.text("INSERT INTO users (id, email, name, role) VALUES (1, :email, :name, 'admin')"),
                 {"email": email, "name": os.environ.get("ADMIN_NAME", "Admin")})
    conn.execute(sa.text("SELECT setval('users_id_seq', 1)"))
    profile = _seed_profile()
    conn.execute(sa.text("INSERT INTO profiles (user_id, targets, scope, skills, llm) "
                         "VALUES (1, :t, :s, :k, :l)"),
                 {k: json.dumps(v) for k, v in zip("tskl", (profile["targets"], profile["scope"], profile["skills"], profile["llm"]))})
    from jobagent.config import get_settings
    resume_path = get_settings().golden_source_file
    if resume_path.exists():
        text = resume_path.read_text()
        sha = hashlib.sha256(text.encode()).hexdigest()
        conn.execute(sa.text("INSERT INTO resumes (id, user_id, filename, text, sha) VALUES (1, 1, :f, :t, :s)"),
                     {"f": resume_path.name, "t": text, "s": sha})
        conn.execute(sa.text("SELECT setval('resumes_id_seq', 1)"))
        conn.execute(sa.text("UPDATE golden_chunks SET user_id = 1, resume_id = 1"))
    else:
        conn.execute(sa.text("UPDATE golden_chunks SET user_id = 1"))
    conn.execute(sa.text(f"""
        INSERT INTO user_jobs (user_id, job_id, in_scope, {', '.join(PER_USER)})
        SELECT 1, id, true, {', '.join(PER_USER)} FROM jobs
        WHERE fit_score IS NOT NULL OR user_label IS NOT NULL OR favorite OR applied_at IS NOT NULL
           OR notes IS NOT NULL OR match_score IS NOT NULL
    """))
    model_path = Path(__file__).resolve().parents[2] / "data" / "preference_model.joblib"
    if model_path.exists():
        conn.execute(sa.text("INSERT INTO preference_models (user_id, meta, blob) VALUES (1, '{}', :b)"),
                     {"b": model_path.read_bytes()})
    op.alter_column('golden_chunks', 'user_id', nullable=False)
    op.create_index('ix_golden_chunks_user_id', 'golden_chunks', ['user_id'])

    # --- jobs loses the per-user columns -------------------------------------------------------
    for idx in ('ix_jobs_fit_score', 'ix_jobs_user_label', 'ix_jobs_favorite'):
        op.execute(f'DROP INDEX IF EXISTS {idx}')
    for col in PER_USER + ['resume_path', 'tailored_at']:
        op.drop_column('jobs', col)
    op.execute("UPDATE jobs SET status = 'deduped' WHERE status = 'scored'")


def downgrade() -> None:
    op.add_column('jobs', sa.Column('tailored_at', TS))
    op.add_column('jobs', sa.Column('resume_path', sa.Text()))
    op.add_column('jobs', sa.Column('notes', sa.Text()))
    op.add_column('jobs', sa.Column('applied_at', TS))
    op.add_column('jobs', sa.Column('favorite', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('jobs', sa.Column('user_labeled_at', TS))
    op.add_column('jobs', sa.Column('user_label', sa.Text()))
    op.add_column('jobs', sa.Column('fit_details', postgresql.JSONB()))
    op.add_column('jobs', sa.Column('fit_score', sa.SmallInteger()))
    op.add_column('jobs', sa.Column('scored_at', TS))
    op.add_column('jobs', sa.Column('golden_source_sha', sa.Text()))
    op.add_column('jobs', sa.Column('match_model', sa.Text()))
    op.add_column('jobs', sa.Column('match_details', postgresql.JSONB()))
    op.add_column('jobs', sa.Column('match_explanation', sa.Text()))
    op.add_column('jobs', sa.Column('match_score', sa.SmallInteger()))
    op.execute(f"""
        UPDATE jobs j SET {', '.join(f'{c} = u.{c}' for c in PER_USER)}
        FROM user_jobs u WHERE u.job_id = j.id AND u.user_id = 1
    """)
    op.create_index('ix_jobs_fit_score', 'jobs', ['fit_score'])
    op.create_index('ix_jobs_user_label', 'jobs', ['user_label'])
    op.create_index('ix_jobs_favorite', 'jobs', ['favorite'])
    op.drop_index('ix_golden_chunks_user_id', table_name='golden_chunks')
    op.drop_column('golden_chunks', 'resume_id')
    op.drop_column('golden_chunks', 'user_id')
    op.drop_column('runs', 'user_id')
    op.drop_table('preference_models')
    op.drop_index('user_jobs_label_idx', table_name='user_jobs')
    op.drop_index('user_jobs_score_idx', table_name='user_jobs')
    op.drop_table('user_jobs')
    op.drop_table('resumes')
    op.drop_table('profiles')
    op.drop_table('users')
