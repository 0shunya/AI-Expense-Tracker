"""initial schema: receipts, versioned extractions, fields, line items, checks, corrections,
review decisions, expenses, model calls, stored files.

Revision ID: 0001
Revises: 
Create Date: 2026-10-28 00:08:12.552410
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('receipts',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=64), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('storage_backend', sa.String(length=16), nullable=False),
    sa.Column('storage_key', sa.String(length=128), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('processing_attempts', sa.Integer(), nullable=False),
    sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('current_extraction_id', sa.Uuid(), nullable=True),
    sa.Column('merchant', sa.String(length=255), nullable=True),
    sa.Column('receipt_date', sa.Date(), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=True),
    sa.Column('total_minor', sa.BigInteger(), nullable=True),
    sa.Column('transaction_reference', sa.String(length=128), nullable=True),
    sa.Column('review_reasons', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('duplicate_status', sa.String(length=16), nullable=False),
    sa.Column('duplicate_of_id', sa.Uuid(), nullable=True),
    sa.Column('duplicate_reasons', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('approved_by', sa.String(length=16), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("duplicate_status IN ('none', 'probable', 'dismissed', 'confirmed')", name='ck_receipts_duplicate_status'),
    sa.CheckConstraint("status IN ('queued', 'processing', 'needs_review', 'approved', 'rejected', 'failed')", name='ck_receipts_status'),
    sa.CheckConstraint('currency IS NULL OR (length(currency) = 3 AND upper(currency) = currency)', name='ck_receipts_currency'),
    sa.CheckConstraint('size_bytes > 0', name='ck_receipts_size'),
    sa.ForeignKeyConstraint(['duplicate_of_id'], ['receipts.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key'),
    sa.UniqueConstraint('sha256')
    )
    op.create_index('ix_receipts_dup_lookup', 'receipts', ['currency', 'total_minor', 'receipt_date'], unique=False)
    op.create_index('ix_receipts_merchant', 'receipts', ['merchant'], unique=False)
    op.create_index('ix_receipts_reference', 'receipts', ['transaction_reference'], unique=False)
    op.create_index('ix_receipts_status_created', 'receipts', ['status', 'created_at'], unique=False)
    op.create_table('stored_files',
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('content', sa.LargeBinary(), nullable=False),
    sa.Column('content_type', sa.String(length=64), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('expenses',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('receipt_id', sa.Uuid(), nullable=False),
    sa.Column('merchant', sa.String(length=255), nullable=False),
    sa.Column('expense_date', sa.Date(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('total_minor', sa.BigInteger(), nullable=False),
    sa.Column('tax_minor', sa.BigInteger(), nullable=True),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('category', sa.String(length=40), nullable=False),
    sa.Column('approved_by', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("kind IN ('purchase', 'refund')", name='ck_expenses_kind'),
    sa.CheckConstraint('length(currency) = 3 AND upper(currency) = currency', name='ck_expenses_currency'),
    sa.CheckConstraint('total_minor >= 0', name='ck_expenses_total_nonnegative'),
    sa.ForeignKeyConstraint(['receipt_id'], ['receipts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('receipt_id', name='uq_expenses_receipt')
    )
    op.create_index('ix_expenses_date', 'expenses', ['expense_date'], unique=False)
    op.create_table('extractions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('receipt_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('trace_id', sa.String(length=36), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('prompt_version', sa.String(length=40), nullable=False),
    sa.Column('passes', sa.Integer(), nullable=False),
    sa.Column('schema_valid', sa.Boolean(), nullable=False),
    sa.Column('schema_errors', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('raw_output', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('readability', sa.String(length=24), nullable=True),
    sa.Column('is_receipt', sa.Boolean(), nullable=True),
    sa.Column('tax_included', sa.Boolean(), nullable=True),
    sa.Column('category', sa.String(length=40), nullable=True),
    sa.Column('notes', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['receipt_id'], ['receipts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('receipt_id', 'version', name='uq_extractions_receipt_version')
    )
    op.create_index(op.f('ix_extractions_receipt_id'), 'extractions', ['receipt_id'], unique=False)
    # receipts <-> extractions reference each other, so this FK is added once both tables exist.
    op.create_foreign_key('fk_receipts_current_extraction', 'receipts', 'extractions', ['current_extraction_id'], ['id'], ondelete='SET NULL')
    op.create_table('model_calls',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('trace_id', sa.String(length=36), nullable=False),
    sa.Column('receipt_id', sa.Uuid(), nullable=True),
    sa.Column('feature', sa.String(length=40), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('prompt_version', sa.String(length=40), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error_category', sa.String(length=40), nullable=True),
    sa.Column('error_message', sa.String(length=300), nullable=True),
    sa.Column('finish_reason', sa.String(length=40), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('thinking_tokens', sa.Integer(), nullable=True),
    sa.Column('total_tokens', sa.Integer(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['receipt_id'], ['receipts.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_model_calls_created', 'model_calls', ['created_at'], unique=False)
    op.create_index(op.f('ix_model_calls_receipt_id'), 'model_calls', ['receipt_id'], unique=False)
    op.create_index(op.f('ix_model_calls_trace_id'), 'model_calls', ['trace_id'], unique=False)
    op.create_table('review_decisions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('receipt_id', sa.Uuid(), nullable=False),
    sa.Column('decision', sa.String(length=24), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('overridden_checks', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("decision IN ('approved', 'rejected', 'duplicate_dismissed', 'duplicate_confirmed', 'reprocess')", name='ck_decisions_decision'),
    sa.ForeignKeyConstraint(['receipt_id'], ['receipts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_review_decisions_receipt_id'), 'review_decisions', ['receipt_id'], unique=False)
    op.create_table('corrections',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('receipt_id', sa.Uuid(), nullable=False),
    sa.Column('extraction_id', sa.Uuid(), nullable=False),
    sa.Column('field', sa.String(length=40), nullable=False),
    sa.Column('action', sa.String(length=16), nullable=False),
    sa.Column('old_value', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('new_value', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("action IN ('corrected', 'confirmed')", name='ck_corrections_action'),
    sa.ForeignKeyConstraint(['extraction_id'], ['extractions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['receipt_id'], ['receipts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_corrections_receipt_created', 'corrections', ['receipt_id', 'created_at'], unique=False)
    op.create_table('extraction_fields',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('extraction_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=40), nullable=False),
    sa.Column('extracted_value', sa.Text(), nullable=True),
    sa.Column('value', sa.Text(), nullable=True),
    sa.Column('source_text', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('extracted', 'uncertain', 'invalid', 'missing', 'verified')", name='ck_fields_status'),
    sa.ForeignKeyConstraint(['extraction_id'], ['extractions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('extraction_id', 'name', name='uq_fields_extraction_name')
    )
    op.create_index(op.f('ix_extraction_fields_extraction_id'), 'extraction_fields', ['extraction_id'], unique=False)
    op.create_table('line_items',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('extraction_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('quantity', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('unit_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('line_total', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('discount', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('tax', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('source_text', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('extracted', 'uncertain', 'invalid', 'missing', 'verified')", name='ck_line_items_status'),
    sa.ForeignKeyConstraint(['extraction_id'], ['extractions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('extraction_id', 'position', name='uq_line_items_position')
    )
    op.create_index(op.f('ix_line_items_extraction_id'), 'line_items', ['extraction_id'], unique=False)
    op.create_table('validation_checks',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('extraction_id', sa.Uuid(), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('severity', sa.String(length=8), nullable=False),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('fields', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("severity IN ('error', 'warning', 'info')", name='ck_checks_severity'),
    sa.ForeignKeyConstraint(['extraction_id'], ['extractions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_validation_checks_extraction_id'), 'validation_checks', ['extraction_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_validation_checks_extraction_id'), table_name='validation_checks')
    op.drop_table('validation_checks')
    op.drop_index(op.f('ix_line_items_extraction_id'), table_name='line_items')
    op.drop_table('line_items')
    op.drop_index(op.f('ix_extraction_fields_extraction_id'), table_name='extraction_fields')
    op.drop_table('extraction_fields')
    op.drop_index('ix_corrections_receipt_created', table_name='corrections')
    op.drop_table('corrections')
    op.drop_index(op.f('ix_review_decisions_receipt_id'), table_name='review_decisions')
    op.drop_table('review_decisions')
    op.drop_index(op.f('ix_model_calls_trace_id'), table_name='model_calls')
    op.drop_index(op.f('ix_model_calls_receipt_id'), table_name='model_calls')
    op.drop_index('ix_model_calls_created', table_name='model_calls')
    op.drop_table('model_calls')
    op.drop_constraint('fk_receipts_current_extraction', 'receipts', type_='foreignkey')
    op.drop_index(op.f('ix_extractions_receipt_id'), table_name='extractions')
    op.drop_table('extractions')
    op.drop_index('ix_expenses_date', table_name='expenses')
    op.drop_table('expenses')
    op.drop_table('stored_files')
    op.drop_index('ix_receipts_status_created', table_name='receipts')
    op.drop_index('ix_receipts_reference', table_name='receipts')
    op.drop_index('ix_receipts_merchant', table_name='receipts')
    op.drop_index('ix_receipts_dup_lookup', table_name='receipts')
    op.drop_table('receipts')
