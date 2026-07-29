-- ============================================================
-- DBA Operations Uptime Tracker — MySQL Views for Excel
-- Version 4.6  |  sla_config support, capacity planning views & database-specific working hours
-- ============================================================

USE dba_tracker;

-- ============================================================
-- WORKING HOURS & HOLIDAYS SETUP
-- ============================================================
CREATE TABLE IF NOT EXISTS uganda_holidays (
    holiday_date DATE PRIMARY KEY,
    name VARCHAR(100)
);

INSERT IGNORE INTO uganda_holidays (holiday_date, name) VALUES 
('2024-01-01', 'New Year'), ('2024-01-26', 'NRM Liberation'), ('2024-02-16', 'Luwum Day'), ('2024-03-08', 'Women Day'), ('2024-03-29', 'Good Friday'), ('2024-04-01', 'Easter Monday'), ('2024-04-10', 'Eid al-Fitr'), ('2024-05-01', 'Labour Day'), ('2024-06-03', 'Martyr Day'), ('2024-06-09', 'Heroes Day'), ('2024-06-17', 'Eid al-Adha'), ('2024-10-09', 'Independence Day'), ('2024-12-25', 'Christmas'), ('2024-12-26', 'Boxing Day'),
('2025-01-01', 'New Year'), ('2025-01-26', 'NRM Liberation'), ('2025-02-16', 'Luwum Day'), ('2025-03-08', 'Women Day'), ('2025-04-18', 'Good Friday'), ('2025-04-21', 'Easter Monday'), ('2025-03-31', 'Eid al-Fitr'), ('2025-05-01', 'Labour Day'), ('2025-06-03', 'Martyr Day'), ('2025-06-09', 'Heroes Day'), ('2025-06-06', 'Eid al-Adha'), ('2025-10-09', 'Independence Day'), ('2025-12-25', 'Christmas'), ('2025-12-26', 'Boxing Day'),
('2026-01-01', 'New Year'), ('2026-01-26', 'NRM Liberation'), ('2026-02-16', 'Luwum Day'), ('2026-03-08', 'Women Day'), ('2026-04-03', 'Good Friday'), ('2026-04-06', 'Easter Monday'), ('2026-03-20', 'Eid al-Fitr'), ('2026-05-01', 'Labour Day'), ('2026-06-03', 'Martyr Day'), ('2026-06-09', 'Heroes Day'), ('2026-05-27', 'Eid al-Adha'), ('2026-10-09', 'Independence Day'), ('2026-12-25', 'Christmas'), ('2026-12-26', 'Boxing Day');

DELIMITER //

DROP FUNCTION IF EXISTS FN_GET_WORKING_SECONDS //
CREATE FUNCTION FN_GET_WORKING_SECONDS(start_dt DATETIME, end_dt DATETIME, db_name VARCHAR(50))
RETURNS INT
DETERMINISTIC
READS SQL DATA
BEGIN
    DECLARE total_sec INT DEFAULT 0;
    DECLARE curr_dt DATETIME;
    DECLARE next_day DATETIME;
    DECLARE step_end DATETIME;
    DECLARE is_holiday INT;
    DECLARE w_start DATETIME;
    DECLARE w_end DATETIME;
    DECLARE is_weekend INT;
    
    IF start_dt IS NULL OR end_dt IS NULL OR start_dt >= end_dt THEN
        RETURN 0;
    END IF;
    
    SET curr_dt = start_dt;
    
    WHILE curr_dt < end_dt DO
        SET next_day = DATE_ADD(DATE(curr_dt), INTERVAL 1 DAY);
        
        IF end_dt < next_day THEN
            SET step_end = end_dt;
        ELSE
            SET step_end = next_day;
        END IF;
        
        SELECT COUNT(*) INTO is_holiday FROM uganda_holidays WHERE holiday_date = DATE(curr_dt);
        SET is_weekend = CASE WHEN WEEKDAY(curr_dt) >= 5 THEN 1 ELSE 0 END;
        
        IF is_holiday = 0 THEN
            IF db_name = 'Oracle' THEN
                -- Oracle: Two shifts, 7 days/week (excluding holidays). Total 18 hrs/day
                -- Window 1: 00:00 to 05:00
                SET w_start = CONCAT(DATE(curr_dt), ' 00:00:00');
                SET w_end = CONCAT(DATE(curr_dt), ' 05:00:00');
                IF curr_dt < w_end AND step_end > w_start THEN
                    SET total_sec = total_sec + TIMESTAMPDIFF(SECOND, GREATEST(curr_dt, w_start), LEAST(step_end, w_end));
                END IF;
                
                -- Window 2: 08:00 to 17:00
                SET w_start = CONCAT(DATE(curr_dt), ' 08:00:00');
                SET w_end = CONCAT(DATE(curr_dt), ' 17:00:00');
                IF curr_dt < w_end AND step_end > w_start THEN
                    SET total_sec = total_sec + TIMESTAMPDIFF(SECOND, GREATEST(curr_dt, w_start), LEAST(step_end, w_end));
                END IF;
                
                -- Window 3: 20:00 to 24:00
                SET w_start = CONCAT(DATE(curr_dt), ' 20:00:00');
                SET w_end = next_day;
                IF curr_dt < w_end AND step_end > w_start THEN
                    SET total_sec = total_sec + TIMESTAMPDIFF(SECOND, GREATEST(curr_dt, w_start), LEAST(step_end, w_end));
                END IF;
            ELSE
                -- Non-Oracle: 1 shift, 8:00 to 17:00, 5 days/week (excluding weekends & holidays)
                IF is_weekend = 0 THEN
                    SET w_start = CONCAT(DATE(curr_dt), ' 08:00:00');
                    SET w_end = CONCAT(DATE(curr_dt), ' 17:00:00');
                    IF curr_dt < w_end AND step_end > w_start THEN
                        SET total_sec = total_sec + TIMESTAMPDIFF(SECOND, GREATEST(curr_dt, w_start), LEAST(step_end, w_end));
                    END IF;
                END IF;
            END IF;
        END IF;
        
        SET curr_dt = next_day;
    END WHILE;
    
    RETURN total_sec;
END //

DROP FUNCTION IF EXISTS FN_GET_MONTH_TARGET_HOURS //
CREATE FUNCTION FN_GET_MONTH_TARGET_HOURS(ym_str VARCHAR(7), db_name VARCHAR(50))
RETURNS INT
DETERMINISTIC
READS SQL DATA
BEGIN
    DECLARE total_hrs INT DEFAULT 0;
    DECLARE curr_dt DATE;
    DECLARE end_dt DATE;
    DECLARE is_holiday INT;
    DECLARE is_weekend INT;
    
    SET curr_dt = STR_TO_DATE(CONCAT(ym_str, '-01'), '%Y-%m-%d');
    SET end_dt = DATE_ADD(curr_dt, INTERVAL 1 MONTH);
    
    WHILE curr_dt < end_dt DO
        SELECT COUNT(*) INTO is_holiday FROM uganda_holidays WHERE holiday_date = curr_dt;
        SET is_weekend = CASE WHEN WEEKDAY(curr_dt) >= 5 THEN 1 ELSE 0 END;
        
        IF is_holiday = 0 THEN
            IF db_name = 'Oracle' THEN
                SET total_hrs = total_hrs + 18;
            ELSE
                IF is_weekend = 0 THEN
                    SET total_hrs = total_hrs + 9;
                END IF;
            END IF;
        END IF;
        
        SET curr_dt = DATE_ADD(curr_dt, INTERVAL 1 DAY);
    END WHILE;
    
    RETURN total_hrs;
END //

DELIMITER ;


-- ============================================================
-- VIEW 1 — INCIDENT REGISTER
-- ============================================================
CREATE OR REPLACE VIEW vw_incident_register AS
SELECT
    ticket                                                              AS `Ticket`,
    DATE(date)                                                          AS `Date`,
    TIME(start)                                                         AS `Start`,
    TIME(end)                                                           AS `End`,
    GREATEST(ROUND(FN_GET_WORKING_SECONDS(start, end, `database`) / 3600.0, 4), 0)  AS `Duration (hrs)`,
    CASE WHEN planned = 1 THEN 'Yes' ELSE 'No' END                     AS `Planned`,
    severity                                                            AS `Severity`,
    `system`                                                            AS `System`,
    root_cause                                                          AS `Root Cause`,
    business_impact                                                     AS `Business Impact`,
    resolved_by                                                         AS `Resolved By`,
    status                                                              AS `Status`,
    `database`                                                          AS `Database`
FROM incident_register;


-- ============================================================
-- VIEW 2 — REPLICATION MONITOR
-- ============================================================
CREATE OR REPLACE VIEW vw_replication_monitor AS
SELECT
    DATE(date)                                                          AS `Check Date`,
    master                                                              AS `Master`,
    replica                                                             AS `Replica`,
    io_thread                                                           AS `IO Thread`,
    sql_thread                                                          AS `SQL Thread`,
    COALESCE(seconds_behind, 0)                                         AS `Seconds Behind`,
    COALESCE(last_error, '')                                            AS `Last Error`,
    CASE
        WHEN io_thread  = 'No'         THEN 'Failed – IO Thread Down'
        WHEN sql_thread = 'No'         THEN 'Failed – SQL Thread Down'
        WHEN io_thread  = 'Connecting' THEN 'Connecting'
        WHEN seconds_behind IS NULL    THEN 'Unknown Lag'
        WHEN seconds_behind  = 0       THEN 'Healthy'
        WHEN seconds_behind  < 10      THEN 'OK (<10 s)'
        WHEN seconds_behind  < 60      THEN 'Degraded (<1 min)'
        ELSE                                'Critical Lag'
    END                                                                 AS `Health Status`,
    `database`                                                          AS `Database`,
    severity                                                            AS `Severity`
FROM replication_monitor;


-- ============================================================
-- VIEW 3 — BACKUP & RECOVERY
-- ============================================================
CREATE OR REPLACE VIEW vw_backup_recovery AS
SELECT
    record_type                                                         AS `Record Type`,
    `database`                                                          AS `Database`,
    DATE(date)                                                          AS `Backup Date`,
    database_instance                                                   AS `Instance`,
    backup_type                                                         AS `Backup Type`,
    backup_used                                                         AS `Backup File / Reference`,
    TIME(start)                                                         AS `Start Time`,
    TIME(end)                                                           AS `End Time`,
    GREATEST(ROUND(TIMESTAMPDIFF(SECOND, start, end) / 3600.0, 4), 0)  AS `Duration (hrs)`,
    outcome                                                             AS `Outcome`,
    location                                                            AS `Location`,
    CASE WHEN verified       = 1 THEN 'Yes' ELSE 'No' END              AS `Verified`,
    CASE WHEN restore_tested = 1 THEN 'Yes' ELSE 'No' END              AS `Restore Tested`,
    CASE WHEN compression    = 1 THEN 'Yes' ELSE 'No' END              AS `Compression`,
    tested_by                                                           AS `Tested By`,
    COALESCE(comments, '')                                              AS `Comments`
FROM backup_recovery;


-- ============================================================
-- VIEW 4 — SERVER HEALTH
-- ============================================================
CREATE OR REPLACE VIEW vw_server_health AS
SELECT
    DATE(date)                                                          AS `Check Date`,
    server                                                              AS `Server`,
    cpu_percent                                                         AS `CPU %`,
    memory_percent                                                      AS `Memory %`,
    disk_percent                                                        AS `Disk %`,
    tablespace_percent                                                  AS `Tablespace %`,
    connections                                                         AS `Connections`,
    slow_queries                                                        AS `Slow Queries`,
    threads                                                             AS `Threads`,
    CASE
        WHEN cpu_percent >= 90 OR memory_percent >= 90
          OR disk_percent >= 90 OR tablespace_percent >= 90            THEN 'Critical'
        WHEN cpu_percent >= 70 OR memory_percent >= 70
          OR disk_percent >= 70 OR tablespace_percent >= 70            THEN 'Warning'
        ELSE                                                                 'Healthy'
    END                                                                 AS `Health Status`,
    `database`                                                          AS `Database`
FROM server_health;


-- ============================================================
-- VIEW 5 — MAINTENANCE REGISTER
-- ============================================================
CREATE OR REPLACE VIEW vw_maintenance_register AS
SELECT
    DATE(date)                                                          AS `Date`,
    change_id                                                           AS `Change ID`,
    activity                                                            AS `Activity`,
    TIME(start)                                                         AS `Start`,
    TIME(end)                                                           AS `End`,
    GREATEST(ROUND(FN_GET_WORKING_SECONDS(start, end, `database`) / 3600.0, 4), 0)  AS `Duration (hrs)`,
    risk                                                                AS `Risk`,
    rollback_plan                                                       AS `Rollback Plan`,
    approved_by                                                         AS `Approved By`,
    CASE WHEN success = 1 THEN 'Yes' ELSE 'No' END                     AS `Success`,
    `database`                                                          AS `Database`
FROM maintenance_register;


-- ============================================================
-- VIEW 6 — CAPACITY PLANNING
-- ============================================================
CREATE OR REPLACE VIEW vw_capacity_planning AS
SELECT
    DATE_FORMAT(month, '%Y-%m')                                         AS `Month-Year`,
    database_size_gb                                                    AS `Database Size (GB)`,
    disk_used_percent                                                   AS `Disk Used %`,
    CASE 
        WHEN `database` IN ('MS SQL Server', 'PostgreSQL') THEN 'N/A'
        ELSE CAST(tablespace_used_percent AS CHAR)
    END                                                                 AS `Tablespace Used %`,
    available_capacity_gb                                               AS `Available (GB)`,
    largest_table                                                       AS `Largest Table`,
    growth_gb                                                           AS `Growth (GB)`,
    `database`                                                          AS `Database`
FROM capacity_planning;


-- ============================================================
-- VIEW 7 — AVAILABILITY CALCULATOR
-- ============================================================
CREATE OR REPLACE VIEW vw_availability_calculator AS
WITH distinct_months AS (
    SELECT DISTINCT DATE_FORMAT(month, '%Y-%m') AS ym FROM sla_config
    UNION
    SELECT DISTINCT DATE_FORMAT(date, '%Y-%m') AS ym FROM incident_register
    UNION
    SELECT DISTINCT DATE_FORMAT(date, '%Y-%m') AS ym FROM maintenance_register
),
all_dbs AS (
    SELECT 'MySQL' AS `database`
    UNION SELECT 'Oracle'
    UNION SELECT 'MS SQL Server'
    UNION SELECT 'PostgreSQL'
),
months_and_dbs AS (
    SELECT m.ym, d.`database`
    FROM distinct_months m
    CROSS JOIN all_dbs d
),
planned_downtime AS (
    SELECT `database`,
           DATE_FORMAT(date, '%Y-%m') AS ym,
           SUM(FN_GET_WORKING_SECONDS(start, end, `database`) / 3600.0) AS total_hrs
    FROM maintenance_register
    GROUP BY `database`, DATE_FORMAT(date, '%Y-%m')
),
unplanned_downtime AS (
    SELECT `database`,
           DATE_FORMAT(date, '%Y-%m') AS ym,
           SUM(FN_GET_WORKING_SECONDS(start, COALESCE(end, NOW()), `database`) / 3600.0) AS total_hrs
    FROM incident_register
    WHERE planned = 0
    GROUP BY `database`, DATE_FORMAT(date, '%Y-%m')
)
SELECT 
    m.ym AS `Month-Year`,
    m.`database` AS `Database`,
    COALESCE(th.target_hours, FN_GET_MONTH_TARGET_HOURS(m.ym, m.`database`)) AS `Target Hours`,
    ROUND(COALESCE(th.target_hours, FN_GET_MONTH_TARGET_HOURS(m.ym, m.`database`)) - COALESCE(u.total_hrs, 0), 4) AS `Hours Achieved`,
    ROUND(COALESCE(p.total_hrs, 0), 4) AS `Planned Downtime (hrs)`,
    ROUND(COALESCE(u.total_hrs, 0), 4) AS `Unplanned Downtime (hrs)`,
    ROUND(
        (COALESCE(th.target_hours, FN_GET_MONTH_TARGET_HOURS(m.ym, m.`database`)) - COALESCE(u.total_hrs, 0)) 
        / NULLIF(COALESCE(th.target_hours, FN_GET_MONTH_TARGET_HOURS(m.ym, m.`database`)), 0) * 100, 
    4) AS `Availability %`,
    COALESCE(th.sla_percent, 99.95) AS `SLA %`,
    CASE 
        WHEN ROUND((COALESCE(th.target_hours, FN_GET_MONTH_TARGET_HOURS(m.ym, m.`database`)) - COALESCE(u.total_hrs, 0)) / NULLIF(COALESCE(th.target_hours, FN_GET_MONTH_TARGET_HOURS(m.ym, m.`database`)), 0) * 100, 4) >= COALESCE(th.sla_percent, 99.95)
        THEN 'Met'
        ELSE 'Missed'
    END AS `SLA Status`
FROM months_and_dbs m
LEFT JOIN planned_downtime p ON m.ym = p.ym AND m.`database` = p.`database`
LEFT JOIN unplanned_downtime u ON m.ym = u.ym AND m.`database` = u.`database`
LEFT JOIN sla_config th ON m.ym = DATE_FORMAT(th.month, '%Y-%m') AND m.`database` = th.database;


-- ============================================================
-- GRANT PERMISSIONS TO EXCEL_USER
-- ============================================================

GRANT SELECT ON dba_tracker.vw_incident_register     TO 'excel_user'@'%';
GRANT SELECT ON dba_tracker.vw_replication_monitor   TO 'excel_user'@'%';
GRANT SELECT ON dba_tracker.vw_backup_recovery       TO 'excel_user'@'%';
GRANT SELECT ON dba_tracker.vw_server_health         TO 'excel_user'@'%';
GRANT SELECT ON dba_tracker.vw_maintenance_register  TO 'excel_user'@'%';
GRANT SELECT ON dba_tracker.vw_capacity_planning     TO 'excel_user'@'%';
GRANT SELECT ON dba_tracker.vw_availability_calculator TO 'excel_user'@'%';

FLUSH PRIVILEGES;
