#!/usr/bin/env python3
"""
MySQL Server Health & Connection Pool Telemetry Agent
=====================================================
Collects OS host resource telemetry (CPU, RAM, Disk) alongside MySQL
database metrics including active connections, max_connections, slow queries,
and running threads.

Computes connection pool utilization percentage against @@GLOBAL.max_connections
and alerts when utilization reaches or exceeds 70% (e.g., 1400 / 2000 connections).

Environments & Targets:
  Production (Default):  http://192.168.35.38:90/api/health/ingest/  (dbtrac.nms.go.ug:90)
  Test / Validation:     http://dbtractest.nms.go.ug:91/api/health/ingest/ (192.168.35.38:91)

Usage:
  # Normal Production Run (Default - targets Prod Port 90):
  python3 mysql_health_agent.py

  # Test Run (Targets Test Port 91 for validation):
  python3 mysql_health_agent.py --test

  # Dual Run (Sends to BOTH Test and Prod simultaneously during validation):
  python3 mysql_health_agent.py --both

  # Custom Ingest URL:
  python3 mysql_health_agent.py --url http://192.168.35.38:91/api/health/ingest/
"""

import os
import sys
import json
import argparse
import urllib.request
import urllib.error
import subprocess
from datetime import datetime

# Production Endpoints (Port 90 on nms360)
DEFAULT_PROD_URLS = [
    "http://192.168.35.38:90/api/health/ingest/",
    "http://dbtrac.nms.go.ug:90/api/health/ingest/",
    "https://dbtrac.nms.go.ug/api/health/ingest/",
    "http://dbtrac.nms.go.ug/api/health/ingest/",
]

# Test Endpoints (Port 91 on dbtractest)
DEFAULT_TEST_URLS = [
    "http://dbtractest.nms.go.ug:91/api/health/ingest/",
    "http://192.168.35.38:91/api/health/ingest/",
]

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
        print(f"Error getting CPU: {e}", file=sys.stderr)
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
        print(f"Error getting Memory: {e}", file=sys.stderr)
    return 0.0


def get_disk_percent():
    """Extracts Disk utilization for the root drive (/)."""
    try:
        output = subprocess.check_output("df -h / | tail -n 1", shell=True).decode('utf-8')
        parts = output.split()
        use_percent = parts[-2].replace('%', '')
        return round(float(use_percent), 2)
    except Exception as e:
        print(f"Error getting Disk: {e}", file=sys.stderr)
    return 0.0


def get_mysql_status(variable_name):
    """Executes a MySQL query to get a specific global status variable."""
    try:
        cmd = f"mysql --defaults-extra-file={MYSQL_CNF_FILE} -e \"SHOW GLOBAL STATUS LIKE '{variable_name}';\" -B -N"
        output = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
        if output:
            return int(output.split()[1])
    except Exception as e:
        print(f"Error getting MySQL status {variable_name}: {e}", file=sys.stderr)
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
        print(f"Warning: Primary connection query failed ({e}), trying fallback SHOW commands...", file=sys.stderr)

    # Fallback if primary query failed or performance_schema is disabled
    if max_connections == 0:
        try:
            cmd = f"mysql --defaults-extra-file={MYSQL_CNF_FILE} -e \"SHOW GLOBAL VARIABLES LIKE 'max_connections';\" -B -N"
            output = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
            if output:
                max_connections = int(output.split()[1])
        except Exception as e:
            print(f"Error getting max_connections: {e}", file=sys.stderr)

    if current_connections == 0 and max_connections > 0:
        try:
            cmd = f"mysql --defaults-extra-file={MYSQL_CNF_FILE} -e \"SHOW GLOBAL STATUS LIKE 'Threads_connected';\" -B -N"
            output = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
            if output:
                current_connections = int(output.split()[1])
        except Exception as e:
            print(f"Error getting Threads_connected: {e}", file=sys.stderr)

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
        "node_name": "Proxmox",
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


def _send_payload_to_urls(payload, urls, label="Target"):
    """Helper to try a list of URLs with fallback until one succeeds."""
    data = json.dumps(payload).encode('utf-8')
    for url in urls:
        req = urllib.request.Request(url, data=data)
        req.add_header('Content-Type', 'application/json')
        req.add_header('X-API-KEY', API_KEY)
        try:
            response = urllib.request.urlopen(req, timeout=8)
            print(f"[{label} - {url}] Successfully ingested health data: {response.read().decode('utf-8')}")
            return True
        except urllib.error.HTTPError as e:
            print(f"[{label} - {url}] HTTP Error {e.code}: {e.read().decode('utf-8', errors='ignore')}", file=sys.stderr)
            if e.code in (400, 401, 403):
                return False
        except Exception as e:
            print(f"[{label} - {url}] Unreachable ({e}), trying next fallback...", file=sys.stderr)
    return False


def send_to_tracker(payload, mode='prod', custom_url=None):
    """
    Sends JSON payload to DBTRAC.
    Modes:
      'prod' (default): Sends to Prod DBTRAC (:90) with fallbacks.
      'test':           Sends to Test DBTRAC (:91) with fallbacks.
      'both':           Sends to BOTH Test (:91) and Prod (:90) simultaneously.
    """
    if custom_url:
        _send_payload_to_urls(payload, [custom_url], label="Custom URL")
        return

    if mode == 'both':
        print("\n--- Dual Ingestion Mode (Test + Prod) ---")
        _send_payload_to_urls(payload, DEFAULT_TEST_URLS, label="TEST (:91)")
        _send_payload_to_urls(payload, DEFAULT_PROD_URLS, label="PROD (:90)")
    elif mode == 'test':
        print("\n--- Test Ingestion Mode (:91) ---")
        _send_payload_to_urls(payload, DEFAULT_TEST_URLS, label="TEST (:91)")
    else:
        # Default: Production
        print("\n--- Production Ingestion Mode (:90) ---")
        _send_payload_to_urls(payload, DEFAULT_PROD_URLS, label="PROD (:90)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DBTRAC MySQL Server Health & Connection Pool Telemetry Agent")
    parser.add_argument("--test", action="store_true", help="Send metrics to Test DBTRAC (dbtractest:91)")
    parser.add_argument("--prod", action="store_true", help="Send metrics to Prod DBTRAC (dbtrac:90) [Default]")
    parser.add_argument("--both", action="store_true", help="Send metrics to BOTH Test (:91) and Prod (:90) simultaneously")
    parser.add_argument("--url", default=None, help="Custom DBTRAC API ingest endpoint")
    args = parser.parse_args()

    # Determine mode: CLI flag takes precedence, then DBTRAC_ENV env var, defaulting to 'prod'
    if args.url:
        mode = 'custom'
    elif args.both:
        mode = 'both'
    elif args.test:
        mode = 'test'
    elif args.prod:
        mode = 'prod'
    else:
        env_mode = os.environ.get("DBTRAC_ENV", "").lower()
        if env_mode in ("both", "dual"):
            mode = 'both'
        elif env_mode in ("test", "testing"):
            mode = 'test'
        else:
            mode = 'prod'  # Always defaults to PROD for production safety

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

    send_to_tracker(payload, mode=mode, custom_url=args.url)
