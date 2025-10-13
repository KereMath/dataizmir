# -*- coding: utf-8 -*-
"""Create spatial_legends table

Revision ID: a8f4e5c9b2d1
Revises: ddce982f5b80
Create Date: 2025-10-13 14:38:47

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = u"a8f4e5c9b2d1"
down_revision = u"ddce982f5b80"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        u"spatial_legends",
        sa.Column(u"id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(u"resource_id", sa.UnicodeText, nullable=False),
        sa.Column(u"color", sa.String(7), nullable=False),  # HEX color format #RRGGBB
        sa.Column(u"description", sa.UnicodeText, nullable=False),
        sa.Column(u"order_index", sa.Integer, server_default=sa.text(u"0")),
        sa.Column(u"created_by", sa.UnicodeText),
        sa.Column(
            u"created_date",
            sa.TIMESTAMP,
            server_default=sa.func.current_timestamp()
        ),
        sa.Column(u"updated_by", sa.UnicodeText),
        sa.Column(u"updated_date", sa.TIMESTAMP)
    )

    # Create index for resource_id lookups
    op.create_index(
        u"idx_spatial_legends_resource_id",
        u"spatial_legends",
        [u"resource_id"]
    )

    # Create foreign key to resource table
    op.create_foreign_key(
        u"fk_spatial_legends_resource",
        u"spatial_legends",
        u"resource",
        [u"resource_id"],
        [u"id"],
        ondelete=u"CASCADE"
    )


def downgrade():
    op.drop_table(u"spatial_legends")
