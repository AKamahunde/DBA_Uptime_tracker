# Database Management Hub - System Architecture

This document provides a technical overview of the Database Management Hub (formerly Atlas). It is intended for future developers and engineers maintaining the application.

## 1. Core Stack
- **Backend:** Django 5.x (Python) providing both the web server and business logic.
- **Database:** MySQL as the primary persistent data store.
- **Frontend:** Server-side rendered HTML via Django Templates, styled with custom CSS focusing on a clean, modern SaaS aesthetic.

## 2. Directory Structure
```
windsurf-project/
├── backend/
│   ├── api/             # Core functional views, forms, models, and templates
│   ├── dba_tracker/     # Main Django settings and routing
│   ├── etl/             # API Ingestion logic for server agents
│   ├── users/           # Custom authentication logic
│   ├── docs/            # Project documentation (User Manual, Architecture)
│   ├── requirements.txt # Python dependencies
│   └── manage.py        # Django CLI
```

## 3. Database Schema Overview
The system relies on several core models defined in `api/models.py`:

- **IncidentRegister:** Tracks unplanned downtime.
    - Uses "Smart Save" logic: If an incident is `Open` or `In Progress`, the `duration_hrs` is dynamically calculated based on the current time minus the `start` time.
    - Fields: `start`, `end`, `duration_hrs`, `status`, `assigned_to`, `severity`, `root_cause`.
- **IncidentChangeLog:** An immutable audit trail tied to `IncidentRegister`.
    - Created automatically in `views.py` when an incident's status or assignment changes, or when a manual comment is provided.
- **BackupRecovery:** Tracks backup executions and restore tests (DR Tests).
    - Differentiates using `record_type` ('Backup' vs 'Recovery').
- **ReplicationMonitor:** Tracks MySQL/PostgreSQL replication status, threads, and seconds behind master.
- **ServerHealth:** Tracks host infrastructure metrics (CPU, Memory, Disk usage).
- **MaintenanceRegister:** Tracks planned downtime (excluded from SLA penalization).

## 4. The ETL Pipeline & API Ingest
To elevate the tool from a manual entry system to an automated monitoring hub, an ETL pipeline allows external scripts (Cron jobs) to push data into the application.

All ingestion endpoints live under `/api/etl/` (routed in `etl/urls.py` and `api/urls.py`).
Authentication is handled via a static API key passed in the `X-API-KEY` header.

### Endpoint: `/api/replication/ingest/`
- **Method:** POST
- **Payload Example:**
  ```json
  {
      "master": "192.168.1.10",
      "replica": "db-replica-01",
      "io_thread": "Yes",
      "sql_thread": "Yes",
      "seconds_behind": 0,
      "last_error": ""
  }
  ```
- **Logic:** Automatically calculates `severity` based on thread status and delay. Saves directly to the `ReplicationMonitor` table.

### Endpoint: `/api/backup/ingest/`
- **Method:** POST
- **Payload Example:**
  ```json
  {
      "database": "MySQL",
      "database_instance": "db-prod-01",
      "backup_type": "Full",
      "start": "2026-07-27T00:00:00Z",
      "end": "2026-07-27T01:30:00Z",
      "outcome": "Success"
  }
  ```
- **Logic:** Records the backup. Ensure the `database` string exactly matches the choices defined in the model (e.g., `MySQL`, `Oracle`) so it maps correctly to the Dashboard SLA filters.

## 5. Security & Permission Model
The hub uses Django's built-in authentication system with custom functional view validations:
- **UI State Restrictions:** Templates explicitly check `can_edit` or `is_admin` flags to hide/show buttons and forms.
- **The 5-Minute Window:** Standard data entrants can create records, but can only edit them if `(timezone.now() - created_at) < 300` seconds. This prevents historical tampering of SLA data.
- **Admin Override:** Users with `is_admin = True` bypass the 5-minute window and can edit or delete any non-system-generated record.

## 6. Business Logic: Dynamic SLAs
The Dashboard (`views.py` -> `dashboard` function) calculates SLA compliance dynamically based on the current month's data.

- **Availability:** Calculated as `100 - (Total Unplanned Downtime / Total Month Hours * 100)`.
- **Target Thresholds:**
    - Availability: `>= 99.50%`
    - MTTR (Mean Time to Repair): `<= 240 mins`
    - Unplanned Downtime: `<= 2 hrs`
    - Replication Lag: `< 2 minutes`
- **Status Determination:** Uses a `get_status(current, target, operator)` helper function to immediately assign a "Meets SLA" (Green) or "Below SLA" (Red) flag for the UI.
