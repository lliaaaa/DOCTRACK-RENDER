"""merge all migration heads into one

Revision ID: merge_all_heads_001
Revises: add_is_temp_admin, add_priority_to_records, add_sub_category_001, a1b2c3d4e5f6
Create Date: 2026-05-29

This is a merge migration — it has no upgrade/downgrade logic.
It simply tells Alembic that all 4 branch heads are now unified
so `flask db upgrade head` works without ambiguity.
"""

revision      = 'merge_all_heads_001'
down_revision = (
    'add_is_temp_admin',
    'add_priority_to_records',
    'add_sub_category_001',
    'a1b2c3d4e5f6',
)
branch_labels = None
depends_on    = None


def upgrade():
    pass   # merge only — no schema changes


def downgrade():
    pass
