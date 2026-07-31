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
    # 1. Gather Target Outage/Incident History
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
