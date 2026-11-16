#!/usr/bin/env python3
"""
MySQL Server Health & Connection Pool Telemetry Agent
=====================================================
Collects OS host resource telemetry (CPU, RAM, Disk) alongside MySQL
database metrics including active connections, max_connections, slow queries,
and running threads.

Computes connection pool utilization percentage against @@GLOBAL.max_connections
and alerts when utilization reaches or exceeds 70% (e.g., 1400 / 2000 connections).

Deploy to:
  Host: 192.168.35.46 (nickledbnew)
  Target Ingest: http://dbtractest.nms.go.ug:91/api/health/ingest/
"""

import os
import json
import urllib.request
import urllib.error
import subprocess
from datetime import datetime

# Configuration
UPTIME_TRACKER_API_URL = os.environ.get("UPTIME_TRACKER_API_URL", "http://192.168.35.38:91/api/health/ingest/")
API_KEY = os.environ.get("REPLICATION_API_KEY", "uptimetracker_api_9f2c1d8e45x9")
SERVER_NAME = os.environ.get("SERVER_NAME", "nickledbnew")
DATABASE_TYPE = "MySQL"
MYSQL_CNF_FILE = os.environ.get("MYSQL_CNF_FILE", os.path.expanduser("~/.my.cnf"))


def get_cpu_percent():
    """Extracts CPU utilization using top command."""
    try:
        output = subprocess.check_output("top -bn1 | grep 'Cpu(s)'", shell=True).decode('utf-8')
        parts = output.split(',')
        idle_part = next((p for p in parts if 'id' in p), None)
        if idle_part:
            idle_val = float(idle_part.replace('id', '').replace('%', '').strip())
            return round(100.0 - idle_val, 2)
    except Exception as e:
        print(f"Error getting CPU: {e}")
    return 0.0


def get_memory_percent():
    """Extracts Memory utilization using free command."""
    try:
        output = subprocess.check_output("free -m | grep Mem", shell=True).decode('utf-8')
        parts = output.split()
        total = float(parts[1])
        used = float(parts[2])
        if total > 0:
            return round((used / total) * 100.0, 2)
    except Exception as e:
        print(f"Error getting Memory: {e}")
    return 0.0


def get_disk_percent():
    """Extracts Disk utilization for the root drive (/)."""
    try:
        output = subprocess.check_output("df -h / | tail -n 1", shell=True).decode('utf-8')
        parts = output.split()
        use_percent = parts[-2].replace('%', '')
        return round(float(use_percent), 2)
    except Exception as e:
        print(f"Error getting Disk: {e}")
    return 0.0


def get_mysql_status(variable_name):
    """Executes a MySQL query to get a specific global status variable."""
    try:
        cmd = f"mysql --defaults-extra-file={MYSQL_CNF_FILE} -e \"SHOW GLOBAL STATUS LIKE '{variable_name}';\" -B -N"
        output = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
        if output:
            return int(output.split()[1])
    except Exception as e:
        print(f"Error getting MySQL status {variable_name}: {e}")
    return 0


def get_mysql_connection_metrics():
    """
    Extracts max_connections and Threads_connected using @@GLOBAL and performance_schema.
    Computes alert_threshold (70%) and utilization_percentage.
    """
    max_connections = 0
    current_connections = 0
    alert_threshold = 0
    utilization_percentage = 0.0

    try:
        # Verified DBA query against global system variable and performance_schema status
        sql = (
            "SELECT "
            "@@GLOBAL.max_connections AS max_connections, "
            "(SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Threads_connected') AS current_connections;"
        )
        cmd = f"mysql --defaults-extra-file={MYSQL_CNF_FILE} -e \"{sql}\" -B -N"
        output = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
        if output:
            parts = output.split()
            if len(parts) >= 2:
                max_connections = int(parts[0])
                current_connections = int(parts[1])
    except Exception as e:
        print(f"Warning: Primary connection query failed ({e}), trying fallback SHOW commands...")

    # Fallback if primary query failed or performance_schema is disabled
    if max_connections == 0:
        try:
            cmd = f"mysql --defaults-extra-file={MYSQL_CNF_FILE} -e \"SHOW GLOBAL VARIABLES LIKE 'max_connections';\" -B -N"
            output = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
            if output:
                max_connections = int(output.split()[1])
        except Exception as e:
            print(f"Error getting max_connections: {e}")

    if current_connections == 0 and max_connections > 0:
        try:
            cmd = f"mysql --defaults-extra-file={MYSQL_CNF_FILE} -e \"SHOW GLOBAL STATUS LIKE 'Threads_connected';\" -B -N"
            output = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
            if output:
                current_connections = int(output.split()[1])
        except Exception as e:
            print(f"Error getting Threads_connected: {e}")

    if max_connections > 0:
        alert_threshold = int(round(max_connections * 0.70))
        utilization_percentage = round((current_connections / max_connections) * 100.0, 2)

    return {
        "max_connections": max_connections,
        "connections": current_connections,
        "alert_threshold": alert_threshold,
        "utilization_percentage": utilization_percentage
    }


def collect_health_data():
    """Collects all OS and database metrics."""
    disk_percent = get_disk_percent()
    conn_metrics = get_mysql_connection_metrics()

    return {
        "server": SERVER_NAME,
        "database": DATABASE_TYPE,
        "cpu_percent": get_cpu_percent(),
        "memory_percent": get_memory_percent(),
        "disk_percent": disk_percent,
        "tablespace_percent": disk_percent,  # Mirrors root disk
        "connections": conn_metrics["connections"],
        "max_connections": conn_metrics["max_connections"],
        "connection_utilization_percent": conn_metrics["utilization_percentage"],
        "threads": get_mysql_status("Threads_running"),
        "slow_queries": get_mysql_status("Slow_queries")
    }


def send_to_tracker(payload):
    """Sends the JSON payload to DBTRAC with test (:91) -> prod fallback."""
    target_urls = list(dict.fromkeys([
        UPTIME_TRACKER_API_URL,
        "http://dbtractest.nms.go.ug:91/api/health/ingest/",
        "http://192.168.35.38:91/api/health/ingest/",
        "http://dbtrac.nms.go.ug:90/api/health/ingest/",
        "http://192.168.35.38:90/api/health/ingest/",
        "https://dbtrac.nms.go.ug/api/health/ingest/",
        "http://dbtrac.nms.go.ug/api/health/ingest/",
    ]))
    data = json.dumps(payload).encode('utf-8')
    for url in target_urls:
        req = urllib.request.Request(url, data=data)
        req.add_header('Content-Type', 'application/json')
        req.add_header('X-API-KEY', API_KEY)
        try:
            response = urllib.request.urlopen(req, timeout=8)
            print(f"[{url}] Successfully ingested health data: {response.read().decode('utf-8')}")
            break
        except urllib.error.HTTPError as e:
            print(f"[{url}] HTTP Error {e.code}: {e.read().decode('utf-8', errors='ignore')}")
            if e.code in (400, 401, 403):
                break
        except Exception as e:
            print(f"[{url}] Unreachable ({e}), trying fallback...")


if __name__ == "__main__":
    print(f"Collecting server health metrics for {SERVER_NAME}...")
    payload = collect_health_data()

    print("\n--- Health Payload ---")
    print(json.dumps(payload, indent=2))
    print("----------------------\n")

    max_c = payload.get("max_connections", 0)
    curr_c = payload.get("connections", 0)
    util = payload.get("connection_utilization_percent", 0.0)
    if max_c > 0:
        thresh = int(round(max_c * 0.70))
        print(f"Connection Pool: {curr_c} / {max_c} ({util}%) | Alert Threshold: 70% (~{thresh})")
        if util >= 90.0:
            print(f"[CRITICAL] Connection utilization is {util}% (>= 90% saturation risk)!")
        elif util >= 70.0:
            print(f"[WARNING] Connection utilization is {util}% (>= 70% threshold reached)!")
        else:
            print(f"[OK] Connection utilization is healthy ({util}%).")

    send_to_tracker(payload)
