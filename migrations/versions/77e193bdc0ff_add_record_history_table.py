"""add record history table

Revision ID: 77e193bdc0ff
Revises: 17bb32609708
Create Date: 2025-12-14 23:03:26.344868
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '77e193bdc0ff'
down_revision = '17bb32609708'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    # Alter 'users' only if it exists and has expected columns
    if 'users' in existing_tables:
        existing_cols = [c['name'] for c in inspector.get_columns('users')]
        if 'role' in existing_cols:
            with op.batch_alter_table('users', schema=None) as batch_op:
                batch_op.alter_column('role',
                       existing_type=sa.VARCHAR(length=50),
                       nullable=False)
        if 'created_at' in existing_cols:
            with op.batch_alter_table('users', schema=None) as batch_op:
                batch_op.alter_column('created_at',
                       existing_type=postgresql.TIMESTAMP(),
                       nullable=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'users' in inspector.get_table_names():
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.alter_column('created_at',
                   existing_type=postgresql.TIMESTAMP(),
                   nullable=True)
            batch_op.alter_column('role',
                   existing_type=sa.VARCHAR(length=50),
                   nullable=True)
