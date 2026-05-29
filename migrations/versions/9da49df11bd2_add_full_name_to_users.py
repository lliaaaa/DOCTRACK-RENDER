"""add full_name to users

Revision ID: 9da49df11bd2
Revises: 77e193bdc0ff
Create Date: 2026-01-05 21:54:41.432742
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '9da49df11bd2'
down_revision = '77e193bdc0ff'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    # Drop legacy 'record' table only if it still exists
    if 'record' in existing_tables:
        op.drop_table('record')

    # Alter 'record_history' only if it exists (legacy — skip on fresh DB)
    if 'record_history' in existing_tables:
        with op.batch_alter_table('record_history', schema=None) as batch_op:
            batch_op.alter_column('timestamp',
                   existing_type=postgresql.TIMESTAMP(),
                   nullable=True)
            try:
                batch_op.drop_constraint(
                    batch_op.f('record_history_record_id_fkey'), type_='foreignkey')
            except Exception:
                pass
            batch_op.create_foreign_key(None, 'records', ['record_id'], ['id'])

    # Alter 'records' only if it exists (legacy — skip on fresh DB)
    if 'records' in existing_tables:
        with op.batch_alter_table('records', schema=None) as batch_op:
            batch_op.alter_column('document_id',
                   existing_type=sa.VARCHAR(length=32),
                   type_=sa.String(length=50),
                   existing_nullable=False)
            batch_op.alter_column('title',
                   existing_type=sa.VARCHAR(length=200),
                   type_=sa.String(length=255),
                   existing_nullable=False)
            batch_op.alter_column('doc_type',
                   existing_type=sa.VARCHAR(length=100),
                   type_=sa.String(length=50),
                   existing_nullable=False)
            batch_op.alter_column('released_by',
                   existing_type=sa.VARCHAR(length=100),
                   nullable=True)
            batch_op.alter_column('received_by',
                   existing_type=sa.VARCHAR(length=100),
                   nullable=True)
            try:
                batch_op.drop_column('remarks')
            except Exception:
                pass
            try:
                batch_op.drop_column('updated_at')
            except Exception:
                pass

    # Add full_name to 'users' — safe since users table exists on fresh DB
    if 'users' in existing_tables:
        existing_cols = [c['name'] for c in inspector.get_columns('users')]
        if 'full_name' not in existing_cols:
            with op.batch_alter_table('users', schema=None) as batch_op:
                batch_op.add_column(
                    sa.Column('full_name', sa.String(length=150), nullable=True))
        col_names = [c['name'] for c in inspector.get_columns('users')]
        if 'department' in col_names:
            with op.batch_alter_table('users', schema=None) as batch_op:
                batch_op.alter_column('department',
                       existing_type=sa.VARCHAR(length=255),
                       type_=sa.String(length=100),
                       existing_nullable=True)


def downgrade():
    if op.get_bind():
        inspector = sa.inspect(op.get_bind())
        existing_tables = inspector.get_table_names()
        if 'users' in existing_tables:
            with op.batch_alter_table('users', schema=None) as batch_op:
                batch_op.alter_column('department',
                       existing_type=sa.String(length=100),
                       type_=sa.VARCHAR(length=255),
                       existing_nullable=True)
                batch_op.drop_column('full_name')
