# Database Management Hub - User Manual

Welcome to the **Database Management Hub** (formerly Uptime Tracker). This tool is designed to help Database Administrators (DBAs) monitor Service Level Agreements (SLAs), track unplanned downtime, and manage infrastructure health across multiple database platforms (MySQL, Oracle, MS SQL Server, and PostgreSQL).

---

## 1. Dashboard Overview

The Dashboard is the central control panel for monitoring your infrastructure. It automatically calculates whether you are meeting your SLA targets based on the data ingested from your servers and the incidents you log manually.

### SLA Color Coding
The dashboard uses a traffic-light color system to immediately highlight issues:
- **Green (Meets SLA):** The metric is within the acceptable threshold.
- **Red (Below SLA):** The metric has breached the target threshold and requires immediate attention.
- **Grey (N/A or No Data):** No data has been logged for this metric yet.

### Filtering by Database
At the top of the dashboard, you will see a summary table showing metrics for all database platforms. Click on any row (e.g., "MySQL") to dynamically update the detailed **Database Detail** table below it, which provides a granular breakdown of backups, replication lag, and failed restore tests for that specific platform.

---

## 2. Managing Incidents

The **Incident Register** is used to log unplanned downtime and track the Mean Time to Repair (MTTR).

### Creating an Incident
1. Click **Incident Register** in the top navigation bar.
2. Click the green **Create New Incident** button.
3. **Smart Save:** You do *not* need to fill out every field! 
   - If the incident is currently happening, leave the `Start` and `End` times blank. The system will automatically use the exact moment you click "Save" as the start time.
   - You can assign the ticket immediately by typing a name in the **Assigned To** field.

### Tracking Duration & SLAs
For any incident with an `Open` or `In Progress` status, the Dashboard will **dynamically calculate the downtime** on the fly. You do not need to manually calculate or update the duration; the dashboard will always show the exact real-time impact on your availability percentage.

### Editing & Escalating Incidents
1. Click **Edit** next to an incident in the list.
2. If you change the **Status** (e.g., to "Resolved"), the system will automatically stamp the current time as the `End` time.
3. If you change the **Assigned To** field, the system will automatically log that the ticket was "Escalated" to the new assignee.
4. You can provide a **Change Comment / Reason** to explain why the status or assignment was changed.

### The History Timeline
Every time an incident is created or edited, an immutable change log is recorded. 
- You can view this log by clicking the **History** button on the Incident List page, or by scrolling to the bottom of the Edit page.
- The timeline shows exactly *who* made the change, *when* they made it, *who* it was assigned to at that moment, and any comments they provided.

---

## 3. Automated Integrations (ETL)

The Database Management Hub is designed to automatically ingest data from your servers. 

If you have the Python agent scripts set up on your servers (e.g., `mysql_replication_agent.py` and `server_health_agent.py`), they will run on a cron schedule and automatically push updates to the hub.
- **Replication Status:** Updates the "Replication Lag" and "Replication Status" metrics on the dashboard.
- **Backups:** Automatically populates the "Last Successful Full Backup" and "Last Successful Incremental Backup" dates.
- **Server Health:** Automatically logs CPU, Memory, and Disk usage to the Server Health Registry.

*Note: Automated records will show "System" or "Script" as the creator.*

---

## 4. Manual Data Entry

If your automated scripts are offline, or if you need to log something manually, you can use the respective registries in the navigation bar:
- **Backup & Recovery Registry:** Log manual backups, or record DR (Disaster Recovery) restore tests. Restore tests are crucial for meeting the "Failed Restore Tests" SLA metric.
- **Maintenance Register:** Log **planned** downtime here. Planned downtime is tracked separately from Incidents (unplanned downtime) and does not negatively impact your availability SLA.
- **Capacity Planning:** Track storage usage, growth rates, and projected capacity exhaustion dates.

---

## 5. Security and Permissions

The system enforces strict data integrity rules:
- **View Only:** Users without specific permissions can view the dashboard and logs, but cannot create or edit records.
- **5-Minute Edit Rule:** If you are a standard user, you can only edit a manual record you created **within 5 minutes** of creating it. After 5 minutes, the record is locked to prevent historical tampering. 
- **Admin Override:** Administrators have full privileges to edit or delete any record at any time, except for automated ETL records (which are immutable to ensure audit accuracy).
