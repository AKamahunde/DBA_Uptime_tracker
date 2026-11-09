#!/usr/bin/env python3
"""
DBITRAC MySQL DR Replication Monitoring Agent
=============================================
Fixes the 3 historical false-positive blind spots:
1. Reads 'Seconds_Behind_Source' (MySQL 8.0.22+) as well as 'Seconds_Behind_Master',
   never defaulting NULL lag to 0.
2. Captures both 'Last_IO_Error' (e.g. Error 1236 purged binary log on Master)
   and 'Last_SQL_Error', along with error numbers and IO thread state.
3. Tracks Binary Log coordinates (Source_Log_File:Read_Source_Log_Pos and
   Relay_Source_Log_File:Exec_Source_Log_Pos) across runs in
   /var/tmp/dbitrac_mysql_repl_state.json to detect a replica silently stuck
   on an old binary log before the Master purges it.
"""
import argparse
import datetime
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request

# Prioritized API target groups (pushes to both DBITRAC :91 and DBTRAC :90,
# automatically falling back from IP to DNS domain names if IP is unreachable after migration)
DEFAULT_TARGET_GROUPS = [
    [
        "http://192.168.35.38:91/api/replication/ingest/",
        "http://dbtractest.nms.go.ug/api/replication/ingest/",
        "http://dbtractest.nms.go.ug:91/api/replication/ingest/",
    ],
    [
        "http://192.168.35.38:90/api/replication/ingest/",
        "http://dbtrac.nms.go.ug/api/replication/ingest/",
        "http://dbtrac.nms.go.ug:90/api/replication/ingest/",
    ],
]
API_KEY = os.environ.get("REPLICATION_API_KEY", "uptimetracker_api_9f2c1d8e45x9")
STATE_FILE = "/var/tmp/dbitrac_mysql_repl_state.json"


def run_mysql_cmd(query, user=None, password=None, host=None):
    """Run a mysql query and return parsed key-value dict from \\G vertical output."""
    cmd = ["mysql"]
    if host:
        cmd.extend(["-h", host])
    if user:
        cmd.extend(["-u", user])
    if password:
        cmd.append(f"-p{password}")
    cmd.extend(["-e", query])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
        output = result.stdout
        status = {}
        for line in output.splitlines():
            line = line.strip()
            if ":" in line and not line.startswith("****"):
                key, val = line.split(":", 1)
                status[key.strip()] = val.strip()
        return status
    except subprocess.CalledProcessError as e:
        print(f"MySQL command failed with exit code {e.returncode}", file=sys.stderr)
        if e.stderr:
            print(f"Stderr: {e.stderr.strip()}", file=sys.stderr)
        return {}
    except Exception as e:
        print(f"Error executing mysql command: {e}", file=sys.stderr)
        return {}


def get_replication_status(user=None, password=None):
    """Runs SHOW REPLICA STATUS (MySQL 8.0.22+) with fallback to SHOW SLAVE STATUS."""
    status = run_mysql_cmd("SHOW REPLICA STATUS\\G", user, password)
    if not status:
        status = run_mysql_cmd("SHOW SLAVE STATUS\\G", user, password)
    if not status:
        print("Error: No replication status returned. Is this a configured replica?", file=sys.stderr)
        sys.exit(1)
    return status


def check_binlog_progression(source_file, read_pos, relay_source_file, exec_pos, io_running, sql_running):
    """
    Compares current binlog coordinates against the previous check stored in STATE_FILE.
    Returns (binlog_stalled: bool, diagnostic_note: str).
    """
    now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    current_coords = f"{source_file}:{read_pos}"
    exec_coords = f"{relay_source_file}:{exec_pos}"

    prev = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                prev = json.load(f)
        except Exception:
            prev = {}

    prev_coords = prev.get("current_coords")
    prev_exec = prev.get("exec_coords")
    unchanged_runs = int(prev.get("unchanged_runs", 0))
    first_seen_at = prev.get("first_seen_at", now_iso)

    if prev_coords == current_coords and prev_exec == exec_coords and source_file:
        unchanged_runs += 1
    else:
        unchanged_runs = 0
        first_seen_at = now_iso

    # Save updated state
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "current_coords": current_coords,
                "exec_coords": exec_coords,
                "unchanged_runs": unchanged_runs,
                "first_seen_at": first_seen_at,
                "updated_at": now_iso,
            }, f, indent=2)
    except Exception:
        pass

    binlog_stalled = False
    notes = []

    if source_file:
        if relay_source_file and relay_source_file != source_file:
            notes.append(f"Binlog Read: {source_file}@{read_pos} | Exec: {relay_source_file}@{exec_pos}")
            if prev_exec == exec_coords:
                binlog_stalled = True
                notes.append(f"STALLED: SQL Exec position stuck on old binlog {relay_source_file}@{exec_pos}")
        else:
            notes.append(f"Binlog: {source_file}@{exec_pos or read_pos}")

        # If both threads claim 'Yes' but binlog coordinates have not moved for >= 2 checks (12+ hours)
        if unchanged_runs >= 2 and io_running == "Yes" and sql_running == "Yes":
            binlog_stalled = True
            notes.append(
                f"STALLED BINLOG: Position {current_coords} has not advanced for {unchanged_runs} consecutive checks (since {first_seen_at})"
            )

    return binlog_stalled, " | ".join(notes)


def send_to_tracker(payload):
    """Sends the JSON payload to configured DBITRAC/DBTRAC endpoints with IP -> DNS fallback."""
    custom_url = os.environ.get("DBITRAC_API_URL") or os.environ.get("DBTRAC_API_URL")
    target_groups = [[custom_url]] if custom_url else DEFAULT_TARGET_GROUPS

    data = json.dumps(payload).encode("utf-8")
    for group in target_groups:
        for url in group:
            req = urllib.request.Request(url, data=data)
            req.add_header("Content-Type", "application/json")
            req.add_header("X-API-KEY", API_KEY)
            try:
                response = urllib.request.urlopen(req, timeout=8)
                resp_data = response.read().decode("utf-8")
                print(f"[{url}] Success: {resp_data}")
                break  # Succeeded for this target environment; move to next group
            except urllib.error.HTTPError as e:
                print(f"[{url}] HTTP Error {e.code}: {e.read().decode('utf-8', errors='ignore')}", file=sys.stderr)
                break
            except urllib.error.URLError as e:
                print(f"[{url}] Unreachable ({e.reason}), trying fallback...", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DBITRAC MySQL DR Replication Monitoring Agent")
    parser.add_argument("-u", "--user", required=False, default=None, help="MySQL user (uses .my.cnf if not provided)")
    parser.add_argument("-p", "--password", required=False, default="", help="MySQL password")
    parser.add_argument("--replica-name", required=False, default="nickeldbdr", help="Replica hostname label")
    args = parser.parse_args()

    status = get_replication_status(args.user, args.password)

    # 1. Read Seconds_Behind_Source (MySQL 8.0.22+) OR Seconds_Behind_Master (legacy)
    raw_lag = status.get("Seconds_Behind_Source")
    if raw_lag is None:
        raw_lag = status.get("Seconds_Behind_Master")

    if raw_lag in (None, "", "NULL"):
        seconds_behind = None
    else:
        try:
            seconds_behind = int(raw_lag)
        except ValueError:
            seconds_behind = None

    # 2. Extract host and thread states
    master_host = status.get("Source_Host") or status.get("Master_Host") or "Unknown"
    io_running = status.get("Replica_IO_Running") or status.get("Slave_IO_Running") or "No"
    sql_running = status.get("Replica_SQL_Running") or status.get("Slave_SQL_Running") or "No"

    # 3. Extract Binary Log Coordinates
    source_log_file = status.get("Source_Log_File") or status.get("Master_Log_File") or ""
    read_source_pos = status.get("Read_Source_Log_Pos") or status.get("Read_Master_Log_Pos") or ""
    relay_source_file = status.get("Relay_Source_Log_File") or status.get("Relay_Master_Log_File") or ""
    exec_source_pos = status.get("Exec_Source_Log_Pos") or status.get("Exec_Master_Log_Pos") or ""

    binlog_stalled, binlog_note = check_binlog_progression(
        source_log_file, read_source_pos, relay_source_file, exec_source_pos, io_running, sql_running
    )

    # 4. Capture BOTH Last_IO_Error and Last_SQL_Error
    io_errno = status.get("Last_IO_Errno") or "0"
    io_error = status.get("Last_IO_Error") or ""
    sql_errno = status.get("Last_SQL_Errno") or status.get("Last_Errno") or "0"
    sql_error = status.get("Last_SQL_Error") or status.get("Last_Error") or ""

    error_parts = []
    if io_error:
        error_parts.append(f"[IO Errno {io_errno}] {io_error}" if io_errno != "0" else f"[IO Error] {io_error}")
    if sql_error:
        error_parts.append(f"[SQL Errno {sql_errno}] {sql_error}" if sql_errno != "0" else f"[SQL Error] {sql_error}")
    if binlog_note:
        error_parts.append(f"[{binlog_note}]")

    last_error = " | ".join(error_parts)

    payload = {
        "master": master_host,
        "replica": args.replica_name or socket.gethostname(),
        "io_thread": io_running,
        "sql_thread": sql_running,
        "seconds_behind": seconds_behind,
        "last_error": last_error,
        "source_log_file": source_log_file,
        "read_source_pos": read_source_pos,
        "relay_source_file": relay_source_file,
        "exec_source_pos": exec_source_pos,
        "binlog_stalled": binlog_stalled,
    }

    print(f"Sending payload to DBITRAC: {json.dumps(payload, indent=2)}")
    send_to_tracker(payload)
