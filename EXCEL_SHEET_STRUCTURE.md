# Excel Sheet Structure Reference

This document provides the exact table structures from the MySQL database to help you create Excel sheets that match the database schema.

## 1. Incident Register Sheet

**Table Name:** `incident_register`  
**View Name:** `vw_incident_register`

| Column Name | Data Type | Description | Notes |
|-------------|-----------|-------------|-------|
| id | Integer | Primary Key | Auto-generated |
| ticket | String (50) | Ticket Number | e.g., INC-001 |
| incident_date | Date | Incident Date | YYYY-MM-DD format |
| start_time | DateTime | Start Time | HH:MM:SS |
| end_time | DateTime | End Time | HH:MM:SS |
| duration_hrs | Decimal (10,2) | Duration in Hours | e.g., 2.50 |
| planned | Boolean | Planned Incident | TRUE/FALSE |
| severity | String (20) | Severity Level | Low, Medium, High, Critical |
| system | String (100) | System Name | e.g., "Production DB" |
| root_cause | Text | Root Cause Analysis | Detailed description |
| business_impact | Text | Business Impact | Detailed description |
| resolved_by | String (100) | Resolved By | Person who resolved |
| status | String (20) | Current Status | Open, In Progress, Resolved, Closed |
| database | String (50) | Database Name | e.g., "dba_tracker" |
| created_at | DateTime | Created Timestamp | Auto-generated |
| updated_at | DateTime | Updated Timestamp | Auto-generated |
| created_by_id | Integer | Created By User ID | Foreign key to users table |

**Excel Column Headers (Recommended):**
```
Ticket | Incident Date | Start Time | End Time | Duration (hrs) | Planned | Severity | System | Root Cause | Business Impact | Resolved By | Status | Database
```

---

## 2. Replication Monitor Sheet

**Table Name:** `replication_monitor`  
**View Name:** `vw_replication_monitor`

| Column Name | Data Type | Description | Notes |
|-------------|-----------|-------------|-------|
| id | Integer | Primary Key | Auto-generated |
| check_date | Date | Check Date | YYYY-MM-DD format |
| master | String (100) | Master Server | e.g., "db-master-01" |
| replica | String (100) | Replica Server | e.g., "db-replica-01" |
| io_thread | String (20) | IO Thread Status | Yes, No, Connecting |
| sql_thread | String (20) | SQL Thread Status | Yes, No, Connecting |
| seconds_behind | Integer | Seconds Behind Master | NULL if running |
| last_error | Text | Last Error Message | Blank if no error |
| status | String (20) | Overall Status | Running, Stopped, Error |
| database | String (50) | Database Name | e.g., "dba_tracker" |
| severity | String (20) | Severity Level | Low, Medium, High, Critical |
| created_at | DateTime | Created Timestamp | Auto-generated |
| updated_at | DateTime | Updated Timestamp | Auto-generated |
| created_by_id | Integer | Created By User ID | Foreign key to users table |

**Excel Column Headers (Recommended):**
```
Check Date | Master | Replica | IO Thread | SQL Thread | Seconds Behind | Last Error | Status | Database | Severity
```

---

## 3. Backup & Recovery Sheet

**Table Name:** `backup_recovery`  
**View Name:** `vw_backup_recovery`

| Column Name | Data Type | Description | Notes |
|-------------|-----------|-------------|-------|
| id | Integer | Primary Key | Auto-generated |
| record_type | String (20) | Record Type | Backup, Recovery |
| database | String (50) | Database Name | e.g., "dba_tracker" |
| backup_date | Date | Backup Date | YYYY-MM-DD format |
| database_instance | String (100) | Database Instance | e.g., "prod-db-01" |
| backup_type | String (20) | Backup Type | Full, Incremental, Differential |
| backup_used | String (50) | Backup Tool Used | e.g., "mysqldump", "rman" |
| start_time | DateTime | Start Time | HH:MM:SS |
| end_time | DateTime | End Time | HH:MM:SS |
| duration_hrs | Decimal (10,2) | Duration in Hours | e.g., 1.25 |
| size_gb | Decimal (10,2) | Backup Size in GB | e.g., 15.50 |
| outcome | String (20) | Backup Outcome | Success, Failed, Partial |
| location | String (200) | Backup Location | e.g., "/backup/weekly/" |
| verified | Boolean | Verification Status | TRUE/FALSE |
| restore_tested | Boolean | Restore Tested | TRUE/FALSE |
| compression | Boolean | Compression Used | TRUE/FALSE |
| retention | String (50) | Retention Policy | e.g., "30 days" |
| tested_by | String (100) | Tested By | Person who tested |
| comments | Text | Additional Comments | Detailed notes |
| created_at | DateTime | Created Timestamp | Auto-generated |
| updated_at | DateTime | Updated Timestamp | Auto-generated |
| created_by_id | Integer | Created By User ID | Foreign key to users table |

**Excel Column Headers (Recommended):**
```
Record Type | Database | Backup Date | Instance | Backup Type | Tool Used | Start Time | End Time | Duration (hrs) | Size (GB) | Outcome | Location | Verified | Restore Tested | Compression | Retention | Tested By | Comments
```

---

## 4. Server Health Sheet

**Table Name:** `server_health`  
**View Name:** `vw_server_health`

| Column Name | Data Type | Description | Notes |
|-------------|-----------|-------------|-------|
| id | Integer | Primary Key | Auto-generated |
| check_date | Date | Check Date | YYYY-MM-DD format |
| server | String (100) | Server Name | e.g., "db-server-01" |
| cpu_percent | Decimal (5,2) | CPU Usage % | 0.00 to 100.00 |
| memory_percent | Decimal (5,2) | Memory Usage % | 0.00 to 100.00 |
| disk_percent | Decimal (5,2) | Disk Usage % | 0.00 to 100.00 |
| tablespace_percent | Decimal (5,2) | Tablespace Usage % | 0.00 to 100.00 |
| connections | Integer | Active Connections | Count |
| slow_queries | Integer | Slow Query Count | Count |
| threads | Integer | Thread Count | Count |
| status | String (20) | Health Status | Healthy, Warning, Critical |
| database | String (50) | Database Name | e.g., "dba_tracker" |
| created_at | DateTime | Created Timestamp | Auto-generated |
| updated_at | DateTime | Updated Timestamp | Auto-generated |
| created_by_id | Integer | Created By User ID | Foreign key to users table |

**Excel Column Headers (Recommended):**
```
Check Date | Server | CPU % | Memory % | Disk % | Tablespace % | Connections | Slow Queries | Threads | Status | Database
```

---

## 5. Maintenance Register Sheet

**Table Name:** `maintenance_register`  
**View Name:** `vw_maintenance_register`

| Column Name | Data Type | Description | Notes |
|-------------|-----------|-------------|-------|
| id | Integer | Primary Key | Auto-generated |
| maintenance_date | Date | Maintenance Date | YYYY-MM-DD format |
| change_id | String (50) | Change ID | e.g., "CHG-001" |
| activity | Text | Maintenance Activity | Detailed description |
| start_time | DateTime | Start Time | HH:MM:SS |
| end_time | DateTime | End Time | HH:MM:SS |
| duration_hrs | Decimal (10,2) | Duration in Hours | e.g., 3.00 |
| risk | String (20) | Risk Level | Low, Medium, High |
| rollback_plan | Text | Rollback Plan | Detailed plan |
| approved_by | String (100) | Approved By | Person who approved |
| success | Boolean | Success Status | TRUE/FALSE |
| database | String (50) | Database Name | e.g., "dba_tracker" |
| created_at | DateTime | Created Timestamp | Auto-generated |
| updated_at | DateTime | Updated Timestamp | Auto-generated |
| created_by_id | Integer | Created By User ID | Foreign key to users table |

**Excel Column Headers (Recommended):**
```
Maintenance Date | Change ID | Activity | Start Time | End Time | Duration (hrs) | Risk | Rollback Plan | Approved By | Success | Database
```

---

## 6. Capacity Planning Sheet

**Table Name:** `capacity_planning`  
**View Name:** `vw_capacity_planning`

| Column Name | Data Type | Description | Notes |
|-------------|-----------|-------------|-------|
| id | Integer | Primary Key | Auto-generated |
| month_year | String (7) | Month-Year | YYYY-MM format (from view) |
| database_size_gb | Decimal (15,2) | Database Size in GB | e.g., 500.75 |
| disk_used_percent | Decimal (5,2) | Disk Usage % | 0.00 to 100.00 |
| tablespace_used_percent | Decimal (5,2) | Tablespace Usage % | 0.00 to 100.00 |
| largest_table | String (100) | Largest Table Name | e.g., "transactions" |
| growth_gb | Decimal (15,2) | Growth in GB | e.g., 25.50 |
| database | String (50) | Database Name | e.g., "dba_tracker" |
| created_at | DateTime | Created Timestamp | Auto-generated |
| updated_at | DateTime | Updated Timestamp | Auto-generated |
| created_by_id | Integer | Created By User ID | Foreign key to users table |

**Excel Column Headers (Recommended):**
```
Month-Year | Database Size (GB) | Disk Used % | Tablespace Used % | Largest Table | Growth (GB) | Database
```

---

## 7. Availability Calculator Sheet

**Table Name:** `availability_calculator`  
**View Name:** `vw_availability_calculator`

| Column Name | Data Type | Description | Notes |
|-------------|-----------|-------------|-------|
| id | Integer | Primary Key | Auto-generated |
| month_year | String (7) | Month-Year | YYYY-MM format (from view) |
| database_type | String (20) | Database Type | ORACLE, MYSQL, MS SQL SERVER |
| hours_achieved | Decimal (10,2) | Hours Achieved | e.g., 720.00 |
| planned_downtime_hrs | Decimal (10,2) | Planned Downtime | e.g., 4.00 |
| unplanned_downtime_hrs | Decimal (10,2) | Unplanned Downtime | e.g., 0.50 |
| target | Decimal (10,2) | Target Hours | e.g., 744.00 |
| availability_percent | Decimal (5,2) | Availability % | 0.00 to 100.00 |
| sla_percent | Decimal (5,2) | SLA % | 0.00 to 100.00 |
| created_at | DateTime | Created Timestamp | Auto-generated |
| updated_at | DateTime | Updated Timestamp | Auto-generated |
| created_by_id | Integer | Created By User ID | Foreign key to users table |

**Excel Column Headers (Recommended):**
```
Month-Year | Database Type | Hours Achieved | Planned Downtime (hrs) | Unplanned Downtime (hrs) | Target | Availability % | SLA %
```

---

## Excel Sheet Setup Instructions

### Step 1: Create Sheets
Create 7 sheets in your Excel workbook with these names:
1. `Incidents`
2. `Replication`
3. `Backups`
4. `Server_Health`
5. `Maintenance`
6. `Capacity`
7. `Availability`

### Step 2: Add Column Headers
Use the recommended column headers from each section above as the first row in each sheet.

### Step 3: Format Columns
Apply appropriate formatting to columns:
- **Date columns**: Date format (YYYY-MM-DD)
- **DateTime columns**: Custom format (YYYY-MM-DD HH:MM:SS)
- **Decimal columns**: Number format with 2 decimal places
- **Percentage columns**: Percentage format
- **Boolean columns**: TRUE/FALSE format
- **Integer columns**: Number format with 0 decimal places

### Step 4: Add Data Validation
For columns with fixed choices, add data validation:
- **Severity**: Low, Medium, High, Critical
- **Status**: Open, In Progress, Resolved, Closed
- **IO Thread/SQL Thread**: Yes, No, Connecting
- **Backup Type**: Full, Incremental, Differential
- **Outcome**: Success, Failed, Partial
- **Health Status**: Healthy, Warning, Critical
- **Risk**: Low, Medium, High
- **Database Type**: ORACLE, MYSQL, MS SQL SERVER

### Step 5: Connect to MySQL
Once sheets are structured correctly:
1. Connect to MySQL via ODBC using excel_user
2. Import data from views (`vw_*`) to replace sample data
3. Update chart references to use the imported data
4. Set up automatic refresh

### Step 6: Test Dashboard
1. Add sample data through Django web app
2. Refresh Excel data
3. Verify charts update correctly
4. Test all calculations and formulas

## Important Notes

- **Views vs Tables**: When connecting to Excel, use the views (`vw_*`) instead of base tables for better formatting and security
- **Date Formatting**: Views separate date and time for easier Excel analysis
- **Sensitive Data**: Views mask user information (e.g., "J****") for privacy
- **Auto-generated Fields**: Fields like `id`, `created_at`, `updated_at`, `created_by_id` are auto-generated by Django
- **Foreign Keys**: `created_by_id` references the users table but can be left blank in Excel

## Quick Reference

| Sheet | View | Key Columns for Charts |
|-------|------|----------------------|
| Incidents | vw_incident_register | incident_date, severity, status |
| Replication | vw_replication_monitor | check_date, status, seconds_behind |
| Backups | vw_backup_recovery | backup_date, outcome, size_gb |
| Server Health | vw_server_health | check_date, cpu_percent, status |
| Maintenance | vw_maintenance_register | maintenance_date, risk, success |
| Capacity | vw_capacity_planning | month_year, disk_used_percent, growth_gb |
| Availability | vw_availability_calculator | month_year, availability_percent, sla_percent |
