#!/usr/bin/env python3
"""
Database migration to add show_on_homepage column to spatial_resources table.
Run this with: . /usr/lib/ckan/default/bin/activate && python migration_homepage_map.py
"""
from sqlalchemy import create_engine, text
import os

# Get database URL from environment or use default
db_url = os.environ.get('CKAN_SQLALCHEMY_URL')

if not db_url:
    # Try to read from config file
    try:
        with open('/etc/ckan/default/ckan.ini', 'r') as f:
            for line in f:
                if line.startswith('sqlalchemy.url'):
                    db_url = line.split('=', 1)[1].strip()
                    break
    except:
        pass

if not db_url:
    print("❌ Could not find database URL")
    print("Please set CKAN_SQLALCHEMY_URL environment variable or check /etc/ckan/default/ckan.ini")
    exit(1)

print(f"🔗 Connecting to database...")

engine = create_engine(db_url)

sql = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'spatial_resources'
        AND column_name = 'show_on_homepage'
    ) THEN
        ALTER TABLE spatial_resources
        ADD COLUMN show_on_homepage BOOLEAN DEFAULT FALSE;
        RAISE NOTICE 'Column show_on_homepage added to spatial_resources table';
    ELSE
        RAISE NOTICE 'Column show_on_homepage already exists in spatial_resources table';
    END IF;
END $$;
"""

try:
    with engine.begin() as conn:
        conn.execute(text(sql))
    print('✅ Migration executed successfully')
    print('📊 Column show_on_homepage has been added to spatial_resources table')
except Exception as e:
    print(f'❌ Migration failed: {str(e)}')
    exit(1)
