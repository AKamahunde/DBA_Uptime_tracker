-- MySQL Script to Create Read-Only User for Excel ODBC Access
-- Run this on your MySQL server to create a user with SELECT-only permissions

-- Create the Excel user with SELECT permissions
CREATE USER IF NOT EXISTS 'excel_user'@'%' IDENTIFIED BY 'Excel@2024';

-- Grant SELECT permissions on all tables in dba_tracker database
GRANT SELECT ON dba_tracker.* TO 'excel_user'@'%';

-- Grant SELECT on views if you create them
-- GRANT SELECT ON dba_tracker.vw_incident_register TO 'excel_user'@'%';
-- GRANT SELECT ON dba_tracker.vw_replication_monitor TO 'excel_user'@'%';
-- GRANT SELECT ON dba_tracker.vw_backup_recovery TO 'excel_user'@'%';
-- GRANT SELECT ON dba_tracker.vw_server_health TO 'excel_user'@'%';
-- GRANT SELECT ON dba_tracker.vw_maintenance_register TO 'excel_user'@'%';
-- GRANT SELECT ON dba_tracker.vw_capacity_planning TO 'excel_user'@'%';
-- GRANT SELECT ON dba_tracker.vw_availability_calculator TO 'excel_user'@'%';

-- Flush privileges to apply changes
FLUSH PRIVILEGES;

-- Verify the user was created
-- SELECT User, Host FROM mysql.user WHERE User = 'excel_user';

-- Verify the grants
-- SHOW GRANTS FOR 'excel_user'@'%';

-- To remove the user later (if needed):
-- DROP USER IF EXISTS 'excel_user'@'%';
-- FLUSH PRIVILEGES;
