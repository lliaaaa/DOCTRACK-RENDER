"""add priority column to records table

Revision ID: add_priority_to_records
Revises: add_remarks_to_record_history
Create Date: 2026-02-24 13:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_priority_to_records'
down_revision = 'add_remarks_to_record_history'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'records' in inspector.get_table_names():
        existing_cols = [c['name'] for c in inspector.get_columns('records')]
        if 'priority' not in existing_cols:
            with op.batch_alter_table('records', schema=None) as batch_op:
                batch_op.add_column(sa.Column('priority', sa.String(length=20),
                                              nullable=False, server_default='Normal'))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'records' in inspector.get_table_names():
        with op.batch_alter_table('records', schema=None) as batch_op:
            batch_op.drop_column('priority')
