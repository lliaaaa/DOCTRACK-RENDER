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
    with op.batch_alter_table('documents') as b:
        b.add_column(sa.Column('workflow_step', sa.String(100), nullable=True))
        b.add_column(sa.Column('arrived_at',   sa.DateTime,    nullable=True))

    with op.batch_alter_table('document_type') as b:
        b.add_column(sa.Column('transaction_category', sa.String(30),
                               nullable=False, server_default='Simple'))
        b.add_column(sa.Column('sla_minutes', sa.Integer,
                               nullable=False, server_default='4320'))

    op.create_table(
        'citizen_charter_config',
        sa.Column('config_id',         sa.Integer, primary_key=True),
        sa.Column('doc_type_id',       sa.Integer,
                  sa.ForeignKey('document_type.document_type_id'), nullable=False),
        sa.Column('department_id',     sa.Integer,
                  sa.ForeignKey('departments.department_id'), nullable=True),
        sa.Column('category',          sa.String(30),  nullable=False, server_default='Simple'),
        sa.Column('sla_minutes',       sa.Integer,     nullable=False, server_default='4320'),
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
