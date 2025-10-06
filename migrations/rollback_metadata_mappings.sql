-- Rollback: Remove metadata mappings feature
-- Date: 2025-10-06
-- Description: Rollback spatial_metadata_mappings table

-- Drop indexes first
DROP INDEX IF EXISTS idx_metadata_mappings_resource_id;
DROP INDEX IF EXISTS idx_metadata_mappings_field_mappings;
DROP INDEX IF EXISTS idx_metadata_mappings_visibility_mode;

-- Drop table
DROP TABLE IF EXISTS spatial_metadata_mappings;

SELECT 'Rollback completed successfully! Table: spatial_metadata_mappings removed.' AS status;
