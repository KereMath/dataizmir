# -*- coding: utf-8 -*-
"""Create spatial_resource_relationships table

Revision ID: ddce982f5b80
Revises: ccd38ad5fced
Create Date: 2025-10-06 11:10:50.416701

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = u"ddce982f5b80"
down_revision = u"ccd38ad5fced"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        u"spatial_resource_relationships",
        sa.Column(u"id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            u"spatial_resource_id",
            sa.UnicodeText,
            sa.ForeignKey(u"resource.id", ondelete=u"CASCADE"),
            nullable=False
        ),
        sa.Column(
            u"related_resource_id",
            sa.UnicodeText,
            sa.ForeignKey(u"resource.id", ondelete=u"CASCADE"),
            nullable=False
        ),
        sa.Column(
            u"created_date",
            sa.TIMESTAMP,
            server_default=sa.func.current_timestamp(),
            nullable=False
        ),
        sa.Column(u"created_by", sa.UnicodeText, nullable=False),
    )

    # Create indexes for better query performance
    op.create_index(
        u"idx_spatial_resource_relationships_spatial_id",
        u"spatial_resource_relationships",
        [u"spatial_resource_id"]
    )
    op.create_index(
        u"idx_spatial_resource_relationships_related_id",
        u"spatial_resource_relationships",
        [u"related_resource_id"]
    )

    # Create unique constraint to prevent duplicate relationships
    op.create_unique_constraint(
        u"uq_spatial_resource_relationship",
        u"spatial_resource_relationships",
        [u"spatial_resource_id", u"related_resource_id"]
    )


def downgrade():
    op.drop_table(u"spatial_resource_relationships")
