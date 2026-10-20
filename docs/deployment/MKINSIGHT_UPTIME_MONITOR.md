# MKInsight Audit Framework & MS SQL Server Uptime Monitor

## Overview
The **MKInsight Uptime Monitor** (`mkinsight_uptime_monitor.ps1`) is an enterprise monitoring agent designed for Windows Server (`Pentana`) running **MKInsight (Pentana Audit)** on **MS SQL Server**.

It operates analogously to `mysql_journal_monitor.py` (MySQL) and `oracle_journal_monitor.py` (Oracle), capturing system-level and service-level outages, calculating precise downtime windows, and automatically ingesting incidents into DBTRAC (`/api/incident/ingest/`).

---

## What It Monitors

1. **System Power Cuts & Dirty Crashes (Windows Event ID 6008, 41)**
   - Detects abrupt power losses, hardware failures, and blue screens (BSOD) where Windows did not cleanly shut down.
   - Correlates the crash timestamp from Event 6008 with the subsequent system startup (Event ID 6005 / Event ID 12).
   - Generates a `[CRITICAL OUTAGE - UNEXPECTED POWER CUT / HARD CRASH]` DBA summary.

2. **Clean Maintenance Reboots (Windows Event ID 1074, 6006 -> 6005)**
   - Detects graceful restarts initiated by Administrators, Windows Update, or maintenance scripts.
   - Captures the initiating user, process, and reason.
   - Correlates with scheduled maintenance windows in DBTRAC (auto-linking to Maintenance Register).

3. **SQL Server Engine Terminations & Crashes (Windows Event ID 7034, 7031)**
   - Detects unexpected termination, crash, or forced kill of the SQL Server database engine (`MSSQLSERVER` / `MSSQL$*`).
   - Identifies restart events (Event ID 7036 entering `running`).
   - Generates a `[CRITICAL OUTAGE - SQL SERVER CRASH / TERMINATION]` DBA summary.

4. **SQL Server Service State Interruptions (Windows Event ID 7036)**
   - Detects when the SQL Server service entered the `stopped` state and when it resumed `running`.
   - Generates a `[SERVICE OUTAGE - SQL SERVER STOPPED]` summary.

5. **Active Real-Time Database & Port Probes**
   - Continuously tests TCP port connectivity (Port 1433) and runs a lightweight SQL probe query (`SELECT @@SERVERNAME, SERVERPROPERTY('ProductVersion')`).
   - Verifies that the audit database (matching `%Live%`, `Pentana`, or `MKInsight`) is in `ONLINE` status.
   - Automatically opens an active outage record if the probe fails, and registers the resolved downtime incident once the service recovers.

6. **State Tracking & Deduplication**
   - Stores the timestamp of the last processed event in `C:\Monitoring\mkinsight_last_event_time.txt` to guarantee events are never double-logged.

---

## Script Configuration Parameters

| Parameter | Default Value | Description |
|---|---|---|
| `$ApiBaseUrl` | `http://192.168.35.38:90/api` | Uptime Tracker / DBTRAC API base URL |
| `$ApiKey` | `uptimetracker_api_9f2c1d8e45x9` | Secure API token configured in web application |
| `$SqlServer` | `localhost` | SQL Server instance name or connection endpoint |
| `$SqlPort` | `1433` | TCP port for SQL Server instance |
| `$ServiceName` | `MSSQLSERVER` | Name of the Windows Service for SQL Server |
| `$Hostname` | `Pentana` | Server hostname registered in dashboard |
| `$DbNameFilter`| `%Live%` | SQL LIKE pattern to match target audit database |
| `$LookbackHours`| `48` | Lookback window in hours when scanning event logs |
| `-Daemon` | `false` | Run in continuous loop mode |
| `$IntervalSec` | `60` | Loop interval in seconds when `-Daemon` is enabled |
| `-DryRun` | `false` | Test detection and print output without posting to API |
| `$StateDirectory`| `C:\Monitoring` | Path to store state and lock files |

---

## Deployment & Setup on Windows Server (Pentana)

### 1. Place the Script on the Host
Copy `mkinsight_uptime_monitor.ps1` to `C:\Monitoring\mkinsight_uptime_monitor.ps1` on the `Pentana` Windows host.

### 2. Verify Execution Policy
Open PowerShell as an Administrator:
```powershell
Set-ExecutionPolicy RemoteSigned -Scope LocalMachine -Force
```

### 3. Run a Dry-Run Test
Test event log detection without sending payloads to the API:
```powershell
powershell.exe -ExecutionPolicy Bypass -File C:\Monitoring\mkinsight_uptime_monitor.ps1 -DryRun
```

### 4. Configure Windows Task Scheduler (Recommended)
To run the monitor automatically every 5 minutes:

1. Open **Task Scheduler** (`taskschd.msc`).
2. Click **Create Task** (not Basic Task):
   - **Name**: `DBTRAC_MKInsight_Uptime_Monitor`
   - **Security options**: Run whether user is logged on or not, with highest privileges.
   - **User Account**: `SYSTEM` or an Administrator account with `Event Log Readers` and SQL Server `CONNECT` privileges.
3. **Triggers**:
   - New Trigger -> **On a schedule** -> Daily.
   - Check **Repeat task every**: `5 minutes` for a duration of `Indefinitely`.
4. **Actions**:
   - Action: **Start a program**
   - Program/script: `powershell.exe`
   - Add arguments:
     ```text
     -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Monitoring\mkinsight_uptime_monitor.ps1"
     ```
5. **Conditions**:
   - Uncheck "Start the task only if the computer is on AC power".
6. Click **OK** and save the task.

---

## Running in Continuous Daemon Mode
If real-time alerting is preferred over Task Scheduler:
```powershell
powershell.exe -ExecutionPolicy Bypass -File C:\Monitoring\mkinsight_uptime_monitor.ps1 -Daemon -IntervalSec 60
```
This checks SQL port connectivity and database health every 60 seconds and immediately registers any detected outage.

---

## SLA Calculation Details
- **Operating Hours**: 08:00 to 17:00 (9 hours/day).
- **Working Days**: Monday through Friday.
- **Exclusions**: Weekends (Saturday, Sunday) and Uganda Public Holidays.
- **SLA Target**: $\ge 99.95\%$ availability.
- Incidents staged by `mkinsight_uptime_monitor.ps1` are calculated against these exact business hours automatically by `calculate_operational_downtime()`.
