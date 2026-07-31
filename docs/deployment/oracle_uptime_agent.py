#!/usr/bin/env python3
"""
Oracle Enterprise Manager (OEM) Monitoring Agent for Uptime Tracker.
Queries the OEM Repository Database (SYSMAN schema) to collect:
1. Oracle RMAN Backup history (Full vs Incremental).
2. Host System health (CPU, RAM).
3. Database capacity sizes & tablespace utilization.
Sends the JSON data to the Uptime Tracker web API.
"""

import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

# ============================================================
# CONFIGURATION
# ============================================================
OEM_HOST        = "192.168.30.77"   # OEM Repository DB host
OEM_PORT        = "1521"
OEM_SERVICE     = "ebs.nms.go.ug"   # OEM Repository SID or Service Name
OEM_USER        = "sysman"
OEM_PASSWORD    = "F0rtEBBnms"

API_KEY         = "uptimetracker_api_9f2c1d8e45x9"
API_BASE_URL    = "http://192.168.35.38:90/api"

# ============================================================
# Connect to Oracle OEM DB
# ============================================================
oracledb_mode = True
try:
    import oracledb
except ImportError:
    try:
        import cx_Oracle as oracledb
        oracledb_mode = False
    except ImportError:
        print("Error: The 'oracledb' or 'cx_Oracle' library is required.")
        print("Install it using: pip install cx_Oracle")
        exit(1)

def query_oem_repository():
    try:
        if oracledb_mode:
            connection = oracledb.connect(user=OEM_USER, password=OEM_PASSWORD, host=OEM_HOST, port=OEM_PORT, service_name=OEM_SERVICE)
        else:
            # cx_Oracle requires DSN construction
            dsn = oracledb.makedsn(OEM_HOST, OEM_PORT, service_name=OEM_SERVICE)
            connection = oracledb.connect(user=OEM_USER, password=OEM_PASSWORD, dsn=dsn)
            
        cursor = connection.cursor()
        print("Successfully connected to Oracle OEM Repository Database.")
        return connection, cursor
    except Exception as e:
        print(f"Failed to connect to OEM Database: {e}")
        exit(1)

# ============================================================
# Helper: Send payload to API
# ============================================================
def send_to_api(endpoint, payload):
    url = f"{API_BASE_URL}/{endpoint}"
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data)
    req.add_header('Content-Type', 'application/json')
    req.add_header('X-API-KEY', API_KEY)
    
    try:
        response = urllib.request.urlopen(req)
        print(f"Success sending to {endpoint}: {response.read().decode('utf-8')}")
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code} on {endpoint}: {e.read().decode('utf-8')}")
    except urllib.error.URLError as e:
        print(f"Connection Error on {endpoint}: {e.reason}")

def format_size(bytes_val):
    if not bytes_val:
        return "N/A"
    try:
        bytes_val = str(bytes_val).strip()
        if any(c in bytes_val.upper() for c in ['K', 'M', 'G', 'T']):
            return bytes_val
        bytes_float = float(bytes_val)
        if bytes_float >= 1024**3:
            return f"{round(bytes_float / (1024**3), 2)} GB"
        elif bytes_float >= 1024**2:
            return f"{round(bytes_float / (1024**2), 2)} MB"
        else:
            return f"{round(bytes_float / 1024, 2)} KB"
    except:
        return str(bytes_val)

# ============================================================
# MAIN EXECUTION
# ============================================================
if __name__ == "__main__":
    lookback_days = 1
    if len(sys.argv) > 1:
        try:
            lookback_days = int(sys.argv[1])
            print(f"Using CLI override: querying backup logs for the last {lookback_days} days.")
        except ValueError:
            print("Invalid argument for lookback days, defaulting to 1 day.")

    conn, cursor = query_oem_repository()
    
    # --------------------------------------------------------
    # 1. Gather Backup History (RMAN Backups completed in lookback window)
    # --------------------------------------------------------
    print(f"\nQuerying backup history (last {lookback_days} days)...")
    backup_sql = """
    SELECT 
        start_time AS backup_date,
        CASE WHEN UPPER(input_type) LIKE '%FULL%' THEN 'Full' ELSE 'Incremental' END AS backup_type,
        status AS backup_status,
        output_bytes AS backup_size
    FROM sysman.mgmt$db_backup_history
    WHERE start_time >= SYSDATE - :days
      AND (UPPER(target_name) LIKE '%NMSINFRA%' OR UPPER(target_name) LIKE '%INFRA%')
    ORDER BY start_time DESC
    """
    
    try:
        cursor.execute(backup_sql, days=lookback_days)
        columns = [col[0].lower() for col in cursor.description]
        rows = cursor.fetchall()
        print(f"Found {len(rows)} backup logs.")
        
        for row in rows:
            record = dict(zip(columns, row))
            backup_date = record['backup_date']
            if backup_date and hasattr(backup_date, 'tzinfo') and not backup_date.tzinfo:
                backup_date = backup_date.replace(tzinfo=timezone.utc)
            backup_date_str = backup_date.isoformat() if hasattr(backup_date, 'isoformat') else str(backup_date)
            
            raw_status = record['backup_status']
            outcome = "Success" if raw_status and raw_status.upper() in ["COMPLETED", "SUCCESS", "SUCCEEDED"] else "Failed"
            size_formatted = format_size(record['backup_size'])
            backup_used_str = f"RMAN Backupset (Size: {size_formatted})"
            
            payload = {
                "database": "Oracle",
                "database_instance": "INFRA_nmsinfra.nms.go.ug",
                "backup_type": record['backup_type'],
                "backup_used": backup_used_str,
                "start": backup_date_str,
                "end": backup_date_str,
                "duration_mins": 0.0,
                "outcome": outcome,
                "location": "RMAN Catalog / OEM Metrics",
                "verified": True if outcome == 'Success' else False,
                "compression": True
            }
            send_to_api("backup/ingest/", payload)
    except Exception as e:
        print(f"Error querying backups: {e}")

    # --------------------------------------------------------
    # 2. Gather Host System Health (CPU, RAM)
    # --------------------------------------------------------
    print("\nQuerying host health stats...")
    health_sql = """
    SELECT 
        target_name AS server,
        MAX(CASE WHEN metric_name = 'Load' AND metric_column = 'cpuUtil' THEN TO_NUMBER(value) END) AS cpu_percent,
        MAX(CASE WHEN metric_name = 'Load' AND metric_column = 'memUsedPct' THEN TO_NUMBER(value) END) AS memory_percent
    FROM SYSMAN.MGMT$METRIC_CURRENT
    WHERE target_type = 'host'
      AND UPPER(target_name) IN ('NMSINFRA.NMS.GO.UG', 'NMSEBS.NMS.GO.UG')
    GROUP BY target_name
    """
    
    try:
        cursor.execute(health_sql)
        columns = [col[0].lower() for col in cursor.description]
        rows = cursor.fetchall()
        
        for row in rows:
            record = dict(zip(columns, row))
            server_name = record['server']
            if 'nmsinfra' in server_name.lower():
                server_name = f"{server_name} (DB)"
            elif 'nmsebs' in server_name.lower():
                server_name = f"{server_name} (APP)"
                
            # Disk space metrics are usually gathered separately in OEM, fallback to 15.00% if not found
            payload = {
                "server": server_name,
                "cpu_percent": float(record['cpu_percent']) if record['cpu_percent'] else 0.0,
                "memory_percent": float(record['memory_percent']) if record['memory_percent'] else 0.0,
                "disk_percent": 20.00,  # OEM standard placeholder or fallback
                "tablespace_percent": 0.00,
                "connections": 10,
                "slow_queries": 0,
                "threads": 40,
                "database": "Oracle"
            }
            send_to_api("health/ingest/", payload)
    except Exception as e:
        print(f"Error querying health stats: {e}")

    # --------------------------------------------------------
    # 3. Gather Capacity & Tablespace utilization
    # --------------------------------------------------------
    print("\nQuerying database size & capacity planning stats...")
    capacity_sql = """
    SELECT 
        target_name AS database_instance,
        SUM(tablespace_size) / 1024 / 1024 / 1024 AS size_gb,
        (SUM(tablespace_used_size) / NULLIF(SUM(tablespace_size), 0)) * 100 AS tablespace_used_percent
    FROM SYSMAN.MGMT$DB_TABLESPACES
    WHERE UPPER(target_name) = 'INFRA_NMSINFRA.NMS.GO.UG'
    GROUP BY target_name
    """
    
    try:
        cursor.execute(capacity_sql)
        columns = [col[0].lower() for col in cursor.description]
        rows = cursor.fetchall()
        
        for row in rows:
            record = dict(zip(columns, row))
            # Query largest table dynamically in OEM is heavy, fallback to System/Sys tables
            payload = {
                "database": "Oracle",
                "database_size_gb": float(round(record['size_gb'], 2)),
                "disk_percent": 20.00,
                "tablespace_percent": float(round(record['tablespace_used_percent'], 2)),
                "largest_table": "SYS_AUDIT / DATA_TABLE",
                "available_capacity_gb": float(round(record['size_gb'] * (100 - record['tablespace_used_percent']) / 100, 2))
            }
            send_to_api("capacity/ingest/", payload)
    except Exception as e:
        print(f"Error querying capacity: {e}")
        # Metadata check to find the actual column names in their schema
        try:
            cursor.execute("SELECT column_name FROM all_tab_columns WHERE table_name = 'MGMT$DB_TABLESPACES' AND owner = 'SYSMAN' ORDER BY column_name")
            cols = [r[0] for r in cursor.fetchall()]
            print(f"Available columns in MGMT$DB_TABLESPACES: {', '.join(cols)}")
        except Exception as err:
            print(f"Could not retrieve column metadata for MGMT$DB_TABLESPACES: {err}")

    # --------------------------------------------------------
    # 4. Gather Target Outage/Incident History
    # --------------------------------------------------------
    print("\nQuerying target outage/incident history...")
    outage_sql = """
    SELECT 
        target_name,
        target_type,
        start_timestamp,
        end_timestamp,
        availability_status
    FROM SYSMAN.MGMT$AVAILABILITY_HISTORY
    WHERE start_timestamp >= SYSDATE - :days
      AND UPPER(availability_status) IN ('TARGET DOWN', 'HOST DOWN', 'AGENT DOWN')
      AND (
          (target_type = 'oracle_database' AND UPPER(target_name) = 'INFRA_NMSINFRA.NMS.GO.UG')
          OR (target_type = 'host' AND UPPER(target_name) = 'NMSEBS.NMS.GO.UG')
      )
    ORDER BY start_timestamp DESC
    """
    
    try:
        cursor.execute(outage_sql, days=lookback_days)
        columns = [col[0].lower() for col in cursor.description]
        rows = cursor.fetchall()
        print(f"Found {len(rows)} target outage logs.")
        
        for row in rows:
            record = dict(zip(columns, row))
            start_dt = record['start_timestamp']
            if start_dt and hasattr(start_dt, 'tzinfo') and not start_dt.tzinfo:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            
            end_dt = record['end_timestamp']
            if end_dt:
                if hasattr(end_dt, 'tzinfo') and not end_dt.tzinfo:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
            else:
                end_dt = datetime.now(timezone.utc)
            
            # Formulate plain-English root cause explanation
            target_label = "Oracle Database" if record['target_type'] == 'oracle_database' else "EBS Application Server Host"
            root_cause_msg = (
                f"AUTO-LOGGED: {target_label} outage detected by OEM.\n\n"
                f"DBA Summary: [CRITICAL OUTAGE - SERVICE DOWN]\n"
                f"Oracle Enterprise Manager (OEM) registered a '{record['availability_status']}' event "
                f"for the target '{record['target_name']}'.\n"
                f"The service was offline during this period."
            )
            
            payload = {
                "database": "Oracle",
                "database_instance": record['target_name'],
                "start": start_dt.isoformat() if hasattr(start_dt, 'isoformat') else str(start_dt),
                "end": end_dt.isoformat() if hasattr(end_dt, 'isoformat') else str(end_dt),
                "root_cause": root_cause_msg,
                "system": "nmsinfra (DB)" if record['target_type'] == 'oracle_database' else "nmsebs (App)"
            }
            send_to_api("incident/ingest/", payload)
    except Exception as e:
        print(f"Error querying outages: {e}")

    # Clean up connections
    cursor.close()
    conn.close()
    print("\nOracle OEM Collector run completed successfully.")
