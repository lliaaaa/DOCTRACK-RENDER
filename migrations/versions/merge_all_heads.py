"""merge all migration heads into one

Revision ID: merge_all_heads_001
Revises: add_is_temp_admin, add_priority_to_records, add_sub_category_001
Create Date: 2026-05-29

Merges the 3 original branch heads into one clean head.
add_audit_log_table then chains off this merge.
"""

revision      = 'merge_all_heads_001'
down_revision = (
    'add_is_temp_admin',
    'add_priority_to_records',
    'add_sub_category_001',
)
branch_labels = None
depends_on    = None


def upgrade():
    pass


def downgrade():
    pass
    
