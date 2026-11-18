#!/usr/bin/env python3
"""
Oracle Direct Database Monitoring Agent for Uptime Tracker.
Connects directly to the Oracle database instance (nmsinfra.nms.go.ug:1530 / INFRA) to collect:
0. Live Oracle engine performance metrics (SESSIONS/PROCESSES, locks, long SQL, cache hit, rollbacks, FRA).
1. Oracle RMAN Backup history (from v$rman_backup_job_details).
2. Database capacity sizes & tablespace utilization (from dba_data_files/dba_free_space).
Supports lightweight 1-hour health-only checks (--health-only via `0 * * * *` cron or `--watch --interval 3600`).
Sends the JSON data to the Uptime Tracker web API.
"""

import os
import sys
import time
import json
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

# ============================================================
# CONFIGURATION
# ============================================================
DB_HOST         = "nmsinfra.nms.go.ug"   # Target Database Hostname
DB_PORT         = 1530
DB_SID          = "INFRA"                # CDB Container SID (where RMAN backups live)
DB_SERVICE_NAME = ""                     # Service Name (blank connects directly to CDB SID INFRA)
DB_USER         = "sys"                  # Database admin user
DB_PASS         = os.environ.get("DB_PASS") or os.environ.get("ORACLE_PASSWORD") or os.environ.get("OEM_PASSWORD", "")

API_KEY         = os.environ.get("REPLICATION_API_KEY", "uptimetracker_api_9f2c1d8e45x9")
API_BASE_URL    = "http://192.168.35.38:90/api"

DEFAULT_PROD_URLS = [
    API_BASE_URL.rstrip('/'),
    "http://dbtrac.nms.go.ug:90/api",
    "https://dbtrac.nms.go.ug/api",
    "http://dbtrac.nms.go.ug/api",
]

DEFAULT_TEST_URLS = [
    "http://dbtractest.nms.go.ug:91/api",
    "http://192.168.35.38:91/api",
]

# ============================================================
# Connect to Oracle DB Directly
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
        exit(1)

def connect_to_database(db_user, db_pass, db_host, db_port, db_sid=None, db_service=None):
    # Try sys as sysdba first (required for CDB backup catalog), fallback to specified user and others
    attempts = [
        {"user": "sys", "mode": "sysdba"},
        {"user": db_user, "mode": None},
        {"user": "apps", "mode": None},
        {"user": "dbsnmp", "mode": None}
    ]
    
    # Avoid duplicate attempts if db_user is already sys or dbsnmp
    unique_attempts = []
    seen = set()
    for att in attempts:
        key = (att["user"].lower(), att["mode"])
        if key not in seen:
            seen.add(key)
            unique_attempts.append(att)

    for att in unique_attempts:
        user = att["user"]
        mode = att["mode"]
        print(f"Attempting connection as '{user}'" + (" AS SYSDBA" if mode == 'sysdba' else "") + "...")
        try:
            # Construct DSN dynamically based on whether service name or SID is used
            if db_service:
                dsn = oracledb.makedsn(db_host, db_port, service_name=db_service)
            else:
                dsn = oracledb.makedsn(db_host, db_port, sid=db_sid)

            if oracledb_mode:
                if mode == 'sysdba':
                    connection = oracledb.connect(user=user, password=db_pass, dsn=dsn, mode=oracledb.SYSDBA)
                else:
                    connection = oracledb.connect(user=user, password=db_pass, dsn=dsn)
            else:
                if mode == 'sysdba':
                    connection = oracledb.connect(user=user, password=db_pass, dsn=dsn, mode=oracledb.SYSDBA)
                else:
                    connection = oracledb.connect(user=user, password=db_pass, dsn=dsn)
            
            cursor = connection.cursor()
            target_desc = f"Service: {db_service}" if db_service else f"SID: {db_sid}"
            print(f"Successfully connected directly to Oracle DB as user '{user}' on {db_host}:{db_port} ({target_desc}).")
            return connection, cursor
        except Exception as e:
            print(f"Failed to connect as user '{user}': {e}")
            
    target_desc = f"Service: {db_service}" if db_service else f"SID: {db_sid}"
    print(f"Error: Could not connect to target database ({target_desc}) on {db_host}:{db_port}.")
    exit(1)

# ============================================================
# Helper: Send payload to API
# ============================================================
def _post_to_base_list(endpoint, payload, base_urls, label="Target"):
    data = json.dumps(payload).encode('utf-8')
    ep_clean = endpoint.lstrip('/')
    seen = set()
    for base_url in base_urls:
        b = base_url.rstrip('/')
        if not b or b in seen:
            continue
        seen.add(b)
        url = f"{b}/{ep_clean}"
        req = urllib.request.Request(url, data=data)
        req.add_header('Content-Type', 'application/json')
        req.add_header('X-API-KEY', API_KEY)
        try:
            response = urllib.request.urlopen(req, timeout=8)
            print(f"[{label} - {url}] Success sending to {endpoint}: {response.read().decode('utf-8')}")
            return True
        except urllib.error.HTTPError as e:
            print(f"[{label} - {url}] HTTP Error {e.code} on {endpoint}: {e.read().decode('utf-8', errors='ignore')}")
            if e.code in (400, 401, 403):
                return False
        except Exception as e:
            print(f"[{label} - {url}] Unreachable ({e}), trying fallback...")
    return False


def send_to_api(endpoint, payload, mode='prod', custom_url=None):
    if custom_url:
        _post_to_base_list(endpoint, payload, [custom_url.rstrip('/')], label="Custom")
        return
    if mode == 'both':
        _post_to_base_list(endpoint, payload, DEFAULT_TEST_URLS, label="TEST (:91)")
        _post_to_base_list(endpoint, payload, DEFAULT_PROD_URLS, label="PROD (:90)")
    elif mode == 'test':
        _post_to_base_list(endpoint, payload, DEFAULT_TEST_URLS, label="TEST (:91)")
    else:
        _post_to_base_list(endpoint, payload, DEFAULT_PROD_URLS, label="PROD (:90)")


def _get_oracle_state_path(sid_name):
    safe_sid = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in (sid_name or "INFRA"))
    return os.path.join(tempfile.gettempdir(), f"dbtrac_oracle_perf_{safe_sid}.json")


def _load_oracle_state(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_oracle_state(path, state):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


def collect_oracle_performance_metrics(cursor, sid_name="INFRA"):
    """
    Collects real-time Oracle instance performance telemetry (at CDB/root scope):
      - v$resource_limit (SESSIONS & PROCESSES current vs limit)
      - v$session (active user sessions, blocking lock chains, queries > 10s)
      - v$session_longops (active long non-RMAN operations > 10s)
      - v$sysstat (Buffer Cache Hit Ratio delta & user rollbacks delta)
      - v$recovery_file_dest (Fast Recovery Area utilization %)
    """
    connections = 0
    max_connections = 0
    try:
        cursor.execute("""
            SELECT LOWER(resource_name), current_utilization, limit_value
            FROM v$resource_limit
            WHERE LOWER(resource_name) IN ('sessions', 'processes')
        """)
        best_pct = -1.0
        for r_name, curr_u, lim_raw in cursor.fetchall():
            try:
                curr_i = int(curr_u or 0)
                lim_str = str(lim_raw or "").strip()
                if not lim_str.isdigit():
                    continue
                lim_i = int(lim_str)
                pct = (curr_i / lim_i * 100.0) if lim_i > 0 else 0.0
                if pct > best_pct or (pct == best_pct and r_name == 'sessions'):
                    best_pct = pct
                    connections = curr_i
                    max_connections = lim_i
            except (TypeError, ValueError):
                pass
    except Exception as e:
        print(f"Warning: v$resource_limit query failed: {e}")

    active_sessions = 0
    blocked_queries = 0
    long_running_queries = 0
    try:
        cursor.execute("""
            SELECT
                COUNT(CASE WHEN type = 'USER' AND status = 'ACTIVE' THEN 1 END),
                COUNT(CASE WHEN blocking_session IS NOT NULL THEN 1 END),
                COUNT(CASE WHEN type = 'USER' AND status = 'ACTIVE' AND wait_class <> 'Idle' AND last_call_et > 10 THEN 1 END)
            FROM v$session
        """)
        s_row = cursor.fetchone()
        if s_row:
            active_sessions = int(s_row[0] or 0)
            blocked_queries = int(s_row[1] or 0)
            long_running_queries = int(s_row[2] or 0)
    except Exception as e:
        print(f"Warning: v$session query failed: {e}")

    long_ops = 0
    try:
        cursor.execute("""
            SELECT COUNT(*)
            FROM v$session_longops
            WHERE totalwork > 0 AND sofar < totalwork AND elapsed_seconds > 10
              AND UPPER(opname) NOT LIKE 'RMAN%'
        """)
        l_row = cursor.fetchone()
        if l_row:
            long_ops = int(l_row[0] or 0)
    except Exception as e:
        print(f"Warning: v$session_longops query failed: {e}")

    sysstat_vals = {}
    try:
        cursor.execute("""
            SELECT name, value
            FROM v$sysstat
            WHERE name IN ('db block gets', 'consistent gets', 'physical reads', 'user rollbacks')
        """)
        for s_name, s_val in cursor.fetchall():
            sysstat_vals[str(s_name)] = int(s_val or 0)
    except Exception as e:
        print(f"Warning: v$sysstat query failed: {e}")

    state_path = _get_oracle_state_path(sid_name)
    prev = _load_oracle_state(state_path)

    def _delta(key):
        curr_v = int(sysstat_vals.get(key, 0))
        if key not in prev:
            return None
        prev_v = int(prev.get(key, 0))
        return max(0, curr_v - prev_v) if curr_v >= prev_v else max(0, curr_v)

    d_db_gets = _delta('db block gets')
    d_cons_gets = _delta('consistent gets')
    d_phys = _delta('physical reads')
    d_rollbacks = _delta('user rollbacks')

    if d_db_gets is not None and d_cons_gets is not None and d_phys is not None and (d_db_gets + d_cons_gets) > 0:
        logical_reads = d_db_gets + d_cons_gets
        cache_hit_pct = round(max(0.0, min(100.0, (1.0 - (d_phys / float(logical_reads))) * 100.0)), 2)
    else:
        tot_logical = int(sysstat_vals.get('db block gets', 0)) + int(sysstat_vals.get('consistent gets', 0))
        tot_phys = int(sysstat_vals.get('physical reads', 0))
        if tot_logical > 0:
            cache_hit_pct = round(max(0.0, min(100.0, (1.0 - (tot_phys / float(tot_logical))) * 100.0)), 2)
        else:
            cache_hit_pct = 100.00

    rollbacks_delta = int(d_rollbacks) if d_rollbacks is not None else 0

    if sysstat_vals:
        _save_oracle_state(state_path, {**sysstat_vals, "updated_at": datetime.now().isoformat()})

    fra_used_pct = 0.0
    try:
        cursor.execute("""
            SELECT ROUND(GREATEST(0, space_used - space_reclaimable) / NULLIF(space_limit, 0) * 100, 2)
            FROM v$recovery_file_dest
            WHERE ROWNUM = 1
        """)
        f_row = cursor.fetchone()
        if f_row and f_row[0] is not None:
            fra_used_pct = round(min(100.0, max(0.0, float(f_row[0]))), 2)
    except Exception as e:
        print(f"Warning: v$recovery_file_dest query failed: {e}")

    return {
        "server": "nmsinfra (DB)",
        "database": "Oracle",
        "node_name": "OLKVM",
        "entered_by": "Script",
        "uptime_percent": 100.0,
        "cpu_percent": 0.0,
        "memory_percent": 0.0,
        "disk_percent": fra_used_pct,
        "tablespace_percent": fra_used_pct,
        "connections": connections,
        "max_connections": max_connections,
        "threads": active_sessions,
        "slow_queries": long_ops,
        "blocked_queries": blocked_queries,
        "long_running_queries": long_running_queries,
        "cache_hit_percent": cache_hit_pct,
        "aborted_connections": rollbacks_delta,
    }

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
    import argparse
    
    parser = argparse.ArgumentParser(description="Oracle Direct DB Agent")
    parser.add_argument("days", type=int, nargs="?", default=7, help="Lookback days (default: 7)")
    parser.add_argument("-u", "--user", default=DB_USER, help="Database username")
    parser.add_argument("-p", "--password", default=DB_PASS, help="Database password")
    parser.add_argument("-H", "--host", default=DB_HOST, help="Database host")
    parser.add_argument("-P", "--port", type=int, default=DB_PORT, help="Database port")
    parser.add_argument("-S", "--sid", default=DB_SID, help="Database SID")
    parser.add_argument("-s", "--service", default=DB_SERVICE_NAME, help="Database Service Name (PDB name)")
    parser.add_argument("--test", action="store_true", help="Send metrics to Test DBTRAC (dbtractest:91)")
    parser.add_argument("--prod", action="store_true", help="Send metrics to Prod DBTRAC (dbtrac:90) [Default]")
    parser.add_argument("--both", action="store_true", help="Send metrics to BOTH Test (:91) and Prod (:90)")
    parser.add_argument("--url", default=None, help="Custom DBTRAC API base URL")
    parser.add_argument("--health-only", action="store_true", help="Only send Oracle instance health/performance metrics (skip backups & capacity)")
    parser.add_argument("--watch", action="store_true", help="Run continuously on --interval seconds (default: 3600s / 1 hour)")
    parser.add_argument("--interval", type=int, default=3600, help="Polling interval in seconds when using --watch (default: 3600 = 1 hour)")

    args = parser.parse_args()
    lookback_days = args.days

    if args.url:
        target_mode = 'custom'
    elif args.both:
        target_mode = 'both'
    elif args.test:
        target_mode = 'test'
    elif args.prod:
        target_mode = 'prod'
    else:
        env_mode = os.environ.get("DBTRAC_ENV", "").lower()
        target_mode = 'both' if env_mode in ("both", "dual") else ('test' if env_mode in ("test", "testing") else 'prod')

    print(f"Starting Oracle Direct DB Agent (Mode: {target_mode}, Lookback: {lookback_days} days)...")

    if args.watch:
        poll_secs = max(60, int(args.interval or 3600))
        print(f"Watch mode active: collecting Oracle health metrics every {poll_secs}s ({round(poll_secs / 3600.0, 2)}h). Press Ctrl+C to stop.")
        while True:
            w_conn = w_cur = None
            try:
                w_conn, w_cur = connect_to_database(args.user, args.password, args.host, args.port, db_sid=args.sid, db_service=args.service)
                perf_payload = collect_oracle_performance_metrics(w_cur, sid_name=args.sid or args.service or "INFRA")
                print(f"Oracle Performance Payload: {json.dumps(perf_payload, indent=2)}")
                send_to_api("health/ingest/", perf_payload, mode=target_mode, custom_url=args.url)
            except KeyboardInterrupt:
                print("\nStopped by user.")
                break
            except Exception as watch_err:
                print(f"Error in watch cycle: {watch_err}")
            finally:
                try:
                    if w_cur:
                        w_cur.close()
                    if w_conn:
                        w_conn.close()
                except Exception:
                    pass
            try:
                time.sleep(poll_secs)
            except KeyboardInterrupt:
                print("\nStopped by user.")
                break
        sys.exit(0)

    conn, cursor = connect_to_database(args.user, args.password, args.host, args.port, db_sid=args.sid, db_service=args.service)

    # --------------------------------------------------------
    # 0. Gather Live Oracle Engine Performance Telemetry
    # --------------------------------------------------------
    print("\nCollecting Oracle instance performance metrics (sessions, locks, long queries, cache hit, FRA)...")
    try:
        perf_payload = collect_oracle_performance_metrics(cursor, sid_name=args.sid or args.service or "INFRA")
        print(f"Oracle Performance Payload: {json.dumps(perf_payload, indent=2)}")
        send_to_api("health/ingest/", perf_payload, mode=target_mode, custom_url=args.url)
    except Exception as perf_err:
        print(f"Error collecting/sending Oracle performance metrics: {perf_err}")

    if args.health_only:
        cursor.close()
        conn.close()
        print("\nOracle Direct DB Health-Only run completed successfully.")
        sys.exit(0)
    
    # --------------------------------------------------------
    # 1. Gather Direct RMAN Backup History
    # --------------------------------------------------------
    print(f"\nQuerying backup history directly from database (last {lookback_days} days)...")
    backup_sql = """
    SELECT 
        start_time AS backup_date,
        CASE WHEN UPPER(input_type) LIKE '%FULL%' THEN 'Full' ELSE 'Incremental' END AS backup_type,
        status AS backup_status,
        elapsed_seconds,
        output_bytes AS backup_size
    FROM v$rman_backup_job_details
    WHERE start_time >= SYSDATE - :days
      AND UPPER(input_type) IN ('DB FULL', 'DB INCR')
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
            outcome = "Success" if raw_status and not any(x in raw_status.upper() for x in ["FAIL", "ERR"]) else "Failed"
            size_formatted = format_size(record['backup_size'])
            backup_used_str = f"RMAN Backupset (Size: {size_formatted})"
            duration_mins = float(record['elapsed_seconds']) / 60.0 if record['elapsed_seconds'] else 0.0
            
            payload = {
                "database": "Oracle",
                "database_instance": "INFRA_nmsinfra.nms.go.ug",
                "backup_type": record['backup_type'],
                "backup_used": backup_used_str,
                "start": backup_date_str,
                "end": backup_date_str,
                "duration_mins": round(duration_mins, 2),
                "outcome": outcome,
                "failure_reason": f"RMAN Job Status: {raw_status}" if outcome == "Failed" else "",
                "location": "Direct DB V$RMAN catalog",
                "verified": True if outcome == 'Success' else False,
                "compression": True
            }
            send_to_api("backup/ingest/", payload, mode=target_mode, custom_url=args.url)
    except Exception as e:
        print(f"Error querying backups: {e}")

    # --------------------------------------------------------
    # 2. Gather Capacity & Tablespace utilization
    # --------------------------------------------------------
    def get_host_disk_metrics(size_gb, free_gb, tablespace_used_percent):
        """
        Attempts to retrieve OS-level disk utilization percent and available space in GB.
        If running locally on the database host, it uses standard OS utility commands.
        Otherwise, it falls back to tablespace metrics to maintain mathematical alignment.
        """
        import shutil
        import subprocess
        import socket
        
        # Check if we are running locally on the target DB host
        hostname = socket.gethostname()
        is_local = "nmsinfra" in hostname.lower() or "localhost" in hostname.lower()
        
        if is_local:
            try:
                total, used, free = shutil.disk_usage("/")
                percent = (used / total) * 100
                return round(percent, 2), round(free / (1024**3), 2)
            except Exception:
                try:
                    output = subprocess.check_output("df -m / | awk 'NR==2 {print $2, $4, $5}'", shell=True).decode('utf-8').strip()
                    parts = output.split()
                    if len(parts) == 3:
                        total_mb = float(parts[0])
                        avail_mb = float(parts[1])
                        percent = float(parts[2].replace('%', ''))
                        return percent, round(avail_mb / 1024, 2)
                except Exception:
                    pass
                    
        # Fallback to tablespace alignment to prevent mathematical contradictions on remote queries
        return round(tablespace_used_percent, 2), round(free_gb, 2)

    print("\nQuerying database size & capacity planning stats directly from database...")
    # In Multitenant Oracle, switch container to PDB NMS for accurate ERP capacity metrics
    try:
        cursor.execute("ALTER SESSION SET CONTAINER = NMS")
        print("Switched session container to PDB 'NMS' for ERP capacity metrics.")
    except Exception as e:
        pass

    try:
        cursor.execute("""
        SELECT 
            (SELECT SUM(bytes)/1024/1024/1024 FROM dba_data_files) AS size_gb,
            (SELECT NVL(SUM(bytes),0)/1024/1024/1024 FROM dba_free_space) AS free_gb
        FROM dual
        """)
        columns = [col[0].lower() for col in cursor.description]
        row = cursor.fetchone()
        
        if row:
            record = dict(zip(columns, row))
            size_gb = float(record['size_gb']) if record['size_gb'] else 0.0
            free_gb = float(record['free_gb']) if record['free_gb'] else 0.0
            used_gb = size_gb - free_gb
            tablespace_used_percent = (used_gb / size_gb) * 100 if size_gb > 0 else 0.0
            
            # Retrieve largest table dynamically with size (excluding system schemas)
            largest_table = "None"
            try:
                # 1st Attempt: Use cdb_segments (Multitenant CDB)
                try:
                    cursor.execute("""
                        SELECT owner, segment_name, bytes FROM (
                            SELECT s.owner, s.segment_name, s.bytes 
                            FROM cdb_segments s
                            JOIN cdb_users u ON s.owner = u.username AND s.con_id = u.con_id
                            WHERE s.segment_type = 'TABLE' 
                              AND u.oracle_maintained = 'N'
                              AND s.owner NOT IN ('SYS', 'SYSTEM', 'SYS_AUDIT', 'AUDSYS')
                              AND s.owner NOT LIKE 'SYS_%'
                            ORDER BY s.bytes DESC
                        ) WHERE ROWNUM = 1
                    """)
                    table_row = cursor.fetchone()
                except Exception:
                    # 2nd Attempt: dba_segments with oracle_maintained flag
                    try:
                        cursor.execute("""
                            SELECT owner, segment_name, bytes FROM (
                                SELECT s.owner, s.segment_name, s.bytes 
                                FROM dba_segments s
                                JOIN dba_users u ON s.owner = u.username
                                WHERE s.segment_type = 'TABLE' 
                                  AND u.oracle_maintained = 'N'
                                  AND s.owner NOT IN ('SYS', 'SYSTEM', 'SYS_AUDIT', 'AUDSYS')
                                  AND s.owner NOT LIKE 'SYS_%'
                                ORDER BY s.bytes DESC
                            ) WHERE ROWNUM = 1
                        """)
                        table_row = cursor.fetchone()
                    except Exception as e_12c:
                        print(f"Oracle query failed: {e_12c}. Retrying with compatibility query...")
                    # 2nd Attempt: Compatibility mode (Oracle 11g) with broad exclusions and LIKE filters
                    cursor.execute("""
                        SELECT owner, segment_name, bytes FROM (
                            SELECT owner, segment_name, bytes FROM dba_segments 
                            WHERE segment_type = 'TABLE' 
                              AND owner NOT IN ('SYS', 'SYSTEM', 'SYS_AUDIT', 'SYSMAN', 'DBSNMP', 'OUTLN', 'MDSYS', 
                                                'ORDSYS', 'ORDDATA', 'CTXSYS', 'XDB', 'WMSYS', 
                                                'MGMT_VIEW', 'OLAPSYS', 'OWBSYS', 'OWBSYS_AUDIT', 
                                                'EXFSYS', 'DVSYS', 'DVF', 'LBACSYS', 'OJVMSYS', 
                                                'SQLTXPLAIN', 'ORDPLUGINS', 'SI_INFORMTN_SCHEMA', 
                                                'APPQOSSYS', 'GSMADMIN_INTERNAL', 'WRI$_RECON_ABSTRACT', 
                                                'AUDSYS', 'GSMUSER', 'GSMCATUSER', 'DBSFWUSER', 'GGSYS', 
                                                'OAC_METADATA', 'REMOTE_SCHEDULER_AGENT', 'DIP', 'SYS$UMF', 
                                                'WRED')
                              AND owner NOT LIKE 'APEX_%'
                              AND owner NOT LIKE 'FLOWS_%'
                              AND owner NOT LIKE 'SYS_%'
                            ORDER BY bytes DESC
                        ) WHERE ROWNUM = 1
                    """)
                    table_row = cursor.fetchone()

                if table_row:
                    owner = table_row[0]
                    name = table_row[1]
                    num_bytes = table_row[2]
                    if num_bytes:
                        gb = num_bytes / (1024 * 1024 * 1024)
                        if gb >= 0.1:
                            size_str = f"{round(gb, 2)} GB"
                        else:
                            mb = num_bytes / (1024 * 1024)
                            size_str = f"{round(mb, 2)} MB"
                        largest_table = f"{owner}.{name} ({size_str})"
                    else:
                        largest_table = f"{owner}.{name}"
            except Exception as table_err:
                print(f"Could not retrieve largest table: {table_err}")
                largest_table = "None"
                
            # Query mathematically aligned host disk space or local stats
            disk_percent, available_capacity_gb = get_host_disk_metrics(size_gb, free_gb, tablespace_used_percent)
            
            payload = {
                "database": "Oracle",
                "database_size_gb": round(used_gb, 2),
                "disk_percent": disk_percent,
                "tablespace_percent": round(tablespace_used_percent, 2),
                "largest_table": largest_table,
                "available_capacity_gb": available_capacity_gb
            }
            print(f"Sending Capacity Payload: {json.dumps(payload, indent=2)}")
            send_to_api("capacity/ingest/", payload, mode=target_mode, custom_url=args.url)
    except Exception as e:
        print(f"Error querying capacity: {e}")

    # Clean up connections
    cursor.close()
    conn.close()
    print("\nOracle Direct DB Monitoring run completed successfully.")
