"""add remarks column to record_history table

Revision ID: add_remarks_to_record_history
Revises: add_remarks_to_records
Create Date: 2026-02-24 12:15:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_remarks_to_record_history'
down_revision = 'add_remarks_to_records'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'record_history' in inspector.get_table_names():
        existing_cols = [c['name'] for c in inspector.get_columns('record_history')]
        if 'remarks' not in existing_cols:
            with op.batch_alter_table('record_history', schema=None) as batch_op:
                batch_op.add_column(sa.Column('remarks', sa.Text(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'record_history' in inspector.get_table_names():
        with op.batch_alter_table('record_history', schema=None) as batch_op:
            batch_op.drop_column('remarks')
