"""Create spatial_legends table

Revision ID: spatial_legends_001
Revises: 
Create Date: 2025-10-13

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'spatial_legends_001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'spatial_legends',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('resource_id', sa.UnicodeText, nullable=False),
        sa.Column('color', sa.String(7), nullable=False),  # HEX color format #RRGGBB
        sa.Column('description', sa.UnicodeText, nullable=False),
        sa.Column('order_index', sa.Integer, default=0),  # Sıralama için
        sa.Column('created_by', sa.UnicodeText),
        sa.Column('created_date', sa.DateTime, server_default=sa.func.current_timestamp()),
        sa.Column('updated_by', sa.UnicodeText),
        sa.Column('updated_date', sa.DateTime, onupdate=sa.func.current_timestamp())
    )
    
    # Index'ler
    op.create_index('idx_spatial_legends_resource_id', 'spatial_legends', ['resource_id'])
    
    # Foreign key (optional, resource tablosuyla ilişki)
    op.create_foreign_key(
        'fk_spatial_legends_resource',
        'spatial_legends', 'resource',
        ['resource_id'], ['id'],
        ondelete='CASCADE'
    )

def downgrade():
    op.drop_table('spatial_legends')
