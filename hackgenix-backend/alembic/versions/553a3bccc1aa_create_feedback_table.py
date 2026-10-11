"""create feedback table

Revision ID: 553a3bccc1aa
Revises:
Create Date: 2026-09-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.models.enums import ProcessingStatus, SourceType

# revision identifiers, used by Alembic.
revision: str = "553a3bccc1aa"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum(SourceType, name="source_type", native_enum=False, validate_strings=True),
            nullable=False,
        ),
        sa.Column("customer_id", sa.String(length=255), nullable=True),
        sa.Column("customer_tier", sa.String(length=50), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "processing_status",
            sa.Enum(
                ProcessingStatus, name="processing_status", native_enum=False, validate_strings=True
            ),
            nullable=False,
        ),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.UniqueConstraint("source", "external_id", name="uq_feedback_source_external_id"),
    )
    op.create_index("ix_feedback_processing_status", "feedback", ["processing_status"])
    op.create_index("ix_feedback_source", "feedback", ["source"])
    op.create_index("ix_feedback_ingested_at", "feedback", ["ingested_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_feedback_ingested_at", table_name="feedback")
    op.drop_index("ix_feedback_source", table_name="feedback")
    op.drop_index("ix_feedback_processing_status", table_name="feedback")
    op.drop_table("feedback")
