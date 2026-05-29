"""Add SLA/charter fields and CitizenCharterConfig table

Revision ID: add_sla_charter_fields
Revises: 17bb32609708
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_sla_charter_fields'
down_revision = '17bb32609708'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if 'documents' in existing_tables:
        existing_cols = [c['name'] for c in inspector.get_columns('documents')]
        cols_to_add = []
        if 'workflow_step' not in existing_cols:
            cols_to_add.append(sa.Column('workflow_step', sa.String(100), nullable=True))
        if 'arrived_at' not in existing_cols:
            cols_to_add.append(sa.Column('arrived_at', sa.DateTime, nullable=True))
        if cols_to_add:
            with op.batch_alter_table('documents') as b:
                for col in cols_to_add:
                    b.add_column(col)

    if 'document_type' in existing_tables:
        existing_cols = [c['name'] for c in inspector.get_columns('document_type')]
        cols_to_add = []
        if 'transaction_category' not in existing_cols:
            cols_to_add.append(sa.Column('transaction_category', sa.String(30),
                                         nullable=False, server_default='Simple'))
        if 'sla_minutes' not in existing_cols:
            cols_to_add.append(sa.Column('sla_minutes', sa.Integer,
                                         nullable=False, server_default='4320'))
        if cols_to_add:
            with op.batch_alter_table('document_type') as b:
                for col in cols_to_add:
                    b.add_column(col)

    if 'citizen_charter_config' not in existing_tables:
        op.create_table(
            'citizen_charter_config',
            sa.Column('config_id',          sa.Integer, primary_key=True),
            sa.Column('doc_type_id',        sa.Integer,
                      sa.ForeignKey('document_type.document_type_id'), nullable=False),
            sa.Column('department_id',      sa.Integer,
                      sa.ForeignKey('departments.department_id'), nullable=True),
            sa.Column('category',           sa.String(30),  nullable=False, server_default='Simple'),
            sa.Column('sla_minutes',        sa.Integer,     nullable=False, server_default='4320'),
            sa.Column('responsible_person', sa.String(150), nullable=True),
        )


def downgrade():
    op.drop_table('citizen_charter_config')
    with op.batch_alter_table('document_type') as b:
        b.drop_column('sla_minutes')
        b.drop_column('transaction_category')
    with op.batch_alter_table('documents') as b:
        b.drop_column('arrived_at')
        b.drop_column('workflow_step')
