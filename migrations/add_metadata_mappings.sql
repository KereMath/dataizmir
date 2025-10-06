-- Migration: Add metadata mappings feature
-- Date: 2025-10-06
-- Description: Add spatial_metadata_mappings table for customizing field display

-- Create metadata mappings table
CREATE TABLE IF NOT EXISTS spatial_metadata_mappings (
    id SERIAL PRIMARY KEY,
    resource_id TEXT NOT NULL UNIQUE REFERENCES resource(id) ON DELETE CASCADE,
    field_mappings JSONB NOT NULL DEFAULT '{}'::jsonb,
    hidden_fields TEXT[] DEFAULT ARRAY[]::TEXT[],
    visibility_mode VARCHAR(20) DEFAULT 'show_all',
    visible_fields TEXT[] DEFAULT ARRAY[]::TEXT[],
    created_by TEXT,
    created_date TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT,
    updated_date TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_metadata_mappings_resource_id ON spatial_metadata_mappings(resource_id);
CREATE INDEX IF NOT EXISTS idx_metadata_mappings_field_mappings ON spatial_metadata_mappings USING GIN(field_mappings);
CREATE INDEX IF NOT EXISTS idx_metadata_mappings_visibility_mode ON spatial_metadata_mappings(visibility_mode);

-- Add comments
COMMENT ON TABLE spatial_metadata_mappings IS 'Stores metadata field name and value mappings for spatial resources';
COMMENT ON COLUMN spatial_metadata_mappings.field_mappings IS 'JSON object mapping original field names to display names and values. Example: {"ILCE": {"displayName": "İlçe", "valueMapping": {"DİKİLİ": "Dikili"}}}';
COMMENT ON COLUMN spatial_metadata_mappings.hidden_fields IS 'Array of field names to hide from popup display (used when visibility_mode=show_all)';
COMMENT ON COLUMN spatial_metadata_mappings.visibility_mode IS 'Visibility mode: show_all (default) or show_selected';
COMMENT ON COLUMN spatial_metadata_mappings.visible_fields IS 'Array of field names to show when visibility_mode=show_selected';

-- Verify table was created
SELECT 'Migration completed successfully! Table: spatial_metadata_mappings created.' AS status;
