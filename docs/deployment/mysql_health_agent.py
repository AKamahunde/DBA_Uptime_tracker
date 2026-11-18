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
import tempfile
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


def get_logical_cpus():
    """Returns OS logical CPU count."""
    try:
        return int(os.cpu_count() or 0)
    except Exception:
        return 0


def get_uptime_days():
    """Reads host uptime in days from /proc/uptime."""
    try:
        with open("/proc/uptime", "r") as f:
            secs = float(f.read().split()[0])
            return round(secs / 86400.0, 2)
    except Exception:
        return 0.0


def get_memory_metrics():
    """Extracts total GB, used GB, and utilization % using free -b."""
    try:
        output = subprocess.check_output("free -b | grep Mem", shell=True).decode('utf-8')
        parts = output.split()
        total_bytes = float(parts[1])
        used_bytes = float(parts[2])
        if total_bytes > 0:
            return (
                round(total_bytes / (1024 ** 3), 2),
                round(used_bytes / (1024 ** 3), 2),
                round((used_bytes / total_bytes) * 100.0, 2),
            )
    except Exception as e:
        print(f"Error getting Memory: {e}", file=sys.stderr)
    return 0.0, 0.0, 0.0


def get_disk_metrics():
    """Extracts root (/) total GB, used GB, and utilization % using df -B1."""
    try:
        output = subprocess.check_output("df -B1 / | tail -n 1", shell=True).decode('utf-8')
        parts = output.split()
        total_bytes = float(parts[1])
        used_bytes = float(parts[2])
        use_percent = float(parts[-2].replace('%', ''))
        return (
            round(total_bytes / (1024 ** 3), 2),
            round(used_bytes / (1024 ** 3), 2),
            round(use_percent, 2),
        )
    except Exception as e:
        print(f"Error getting Disk: {e}", file=sys.stderr)
    return 0.0, 0.0, 0.0


def _run_mysql_query(sql):
    """Runs a MySQL query using ~/.my.cnf and returns stripped stdout lines."""
    cmd = [
        "mysql",
        f"--defaults-extra-file={MYSQL_CNF_FILE}",
        "-B",
        "-N",
        "-e",
        sql,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or f"mysql exited with {res.returncode}")
    return [line for line in res.stdout.splitlines() if line.strip()]


def get_mysql_status(variable_name):
    """Executes a MySQL query to get a specific global status variable."""
    try:
        lines = _run_mysql_query(f"SHOW GLOBAL STATUS LIKE '{variable_name}';")
        if lines:
            return int(float(lines[0].split()[1]))
    except Exception as e:
        print(f"Error getting MySQL status {variable_name}: {e}", file=sys.stderr)
    return 0


def _get_state_file_path():
    safe_srv = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in SERVER_NAME)
    return os.path.join(tempfile.gettempdir(), f"dbtrac_mysql_perf_{safe_srv}.json")


def _load_perf_state():
    path = _get_state_file_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_perf_state(state):
    path = _get_state_file_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


def get_mysql_performance_metrics():
    """
    Collects comprehensive MySQL engine performance metrics:
      - Connections & saturation (max_connections, Threads_connected, Threads_running)
      - Slow query delta (since last check, avoiding misleading cumulative totals)
      - Aborted connection delta
      - Active long-running queries (> 10s)
      - Blocked sessions (metadata/table locks + InnoDB LOCK WAIT transactions)
      - InnoDB Buffer Pool Hit Ratio (%)
    """
    status_vars = {}
    try:
        status_sql = (
            "SHOW GLOBAL STATUS WHERE Variable_name IN ("
            "'Threads_connected','Threads_running','Slow_queries','Aborted_connects',"
            "'Innodb_buffer_pool_read_requests','Innodb_buffer_pool_reads'"
            ");"
        )
        for line in _run_mysql_query(status_sql):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    status_vars[parts[0]] = int(float(parts[1]))
                except ValueError:
                    pass
    except Exception as e:
        print(f"Warning: batch GLOBAL STATUS failed ({e}), trying fallback...", file=sys.stderr)

    max_connections = 0
    current_connections = status_vars.get("Threads_connected", 0)
    threads_running = status_vars.get("Threads_running", 0)

    try:
        lines = _run_mysql_query("SELECT @@GLOBAL.max_connections;")
        if lines:
            max_connections = int(float(lines[0].split()[0]))
    except Exception as e:
        print(f"Warning: @@GLOBAL.max_connections query failed ({e})", file=sys.stderr)
        try:
            lines = _run_mysql_query("SHOW GLOBAL VARIABLES LIKE 'max_connections';")
            if lines:
                max_connections = int(float(lines[0].split()[1]))
        except Exception:
            pass

    if current_connections == 0 and max_connections > 0:
        current_connections = get_mysql_status("Threads_connected")
    if threads_running == 0:
        threads_running = get_mysql_status("Threads_running")

    # Active long queries (> 10s) and metadata/table lock waits from PROCESSLIST
    long_running_queries = 0
    meta_lock_waits = 0
    try:
        proc_sql = (
            "SELECT "
            "COALESCE(SUM(CASE WHEN COMMAND <> 'Sleep' AND TIME > 10 AND USER NOT IN ('system user','event_scheduler') THEN 1 ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN LOWER(COALESCE(STATE,'')) LIKE '%metadata lock%' OR LOWER(COALESCE(STATE,'')) LIKE '%table lock%' THEN 1 ELSE 0 END), 0) "
            "FROM information_schema.PROCESSLIST;"
        )
        lines = _run_mysql_query(proc_sql)
        if lines:
            parts = lines[0].split()
            if len(parts) >= 2:
                long_running_queries = int(parts[0])
                meta_lock_waits = int(parts[1])
    except Exception as e:
        print(f"Warning: PROCESSLIST inspection failed ({e})", file=sys.stderr)

    # InnoDB row-lock wait transactions (works on MySQL 5.7 & 8.0)
    innodb_lock_waits = 0
    try:
        lines = _run_mysql_query("SELECT COUNT(*) FROM information_schema.INNODB_TRX WHERE trx_state = 'LOCK WAIT';")
        if lines:
            innodb_lock_waits = int(lines[0].split()[0])
    except Exception:
        pass

    blocked_queries = meta_lock_waits + innodb_lock_waits

    # Compute counter deltas since previous agent run
    prev_state = _load_perf_state()
    curr_slow = status_vars.get("Slow_queries", get_mysql_status("Slow_queries"))
    curr_aborted = status_vars.get("Aborted_connects", get_mysql_status("Aborted_connects"))
    curr_bp_req = status_vars.get("Innodb_buffer_pool_read_requests", get_mysql_status("Innodb_buffer_pool_read_requests"))
    curr_bp_reads = status_vars.get("Innodb_buffer_pool_reads", get_mysql_status("Innodb_buffer_pool_reads"))

    def _calc_delta(curr_val, key):
        if key not in prev_state:
            return 0  # Baseline run: avoid dumping lifetime totals from before monitoring
        prev_val = int(prev_state.get(key, 0))
        return max(0, curr_val - prev_val) if curr_val >= prev_val else max(0, curr_val)

    slow_queries_delta = _calc_delta(curr_slow, "Slow_queries")
    aborted_delta = _calc_delta(curr_aborted, "Aborted_connects")

    # Prefer interval InnoDB buffer pool hit ratio; fall back to cumulative if interval has no reads/requests
    d_req = _calc_delta(curr_bp_req, "Innodb_buffer_pool_read_requests") if "Innodb_buffer_pool_read_requests" in prev_state else curr_bp_req
    d_reads = _calc_delta(curr_bp_reads, "Innodb_buffer_pool_reads") if "Innodb_buffer_pool_reads" in prev_state else curr_bp_reads
    if d_req > 0:
        cache_hit_pct = round(max(0.0, min(100.0, (1.0 - (d_reads / float(d_req))) * 100.0)), 2)
    elif curr_bp_req > 0:
        cache_hit_pct = round(max(0.0, min(100.0, (1.0 - (curr_bp_reads / float(curr_bp_req))) * 100.0)), 2)
    else:
        cache_hit_pct = 100.00

    _save_perf_state({
        "Slow_queries": curr_slow,
        "Aborted_connects": curr_aborted,
        "Innodb_buffer_pool_read_requests": curr_bp_req,
        "Innodb_buffer_pool_reads": curr_bp_reads,
        "updated_at": datetime.now().isoformat(),
    })

    utilization_percentage = round((current_connections / max_connections) * 100.0, 2) if max_connections > 0 else 0.0

    return {
        "max_connections": max_connections,
        "connections": current_connections,
        "threads": threads_running,
        "utilization_percentage": utilization_percentage,
        "slow_queries": slow_queries_delta,
        "long_running_queries": long_running_queries,
        "blocked_queries": blocked_queries,
        "cache_hit_percent": cache_hit_pct,
        "aborted_connections": aborted_delta,
    }


def collect_health_data():
    """Collects all OS and database engine performance metrics."""
    mem_total_gb, mem_used_gb, mem_percent = get_memory_metrics()
    disk_total_gb, disk_used_gb, disk_percent = get_disk_metrics()
    perf = get_mysql_performance_metrics()

    return {
        "server": SERVER_NAME,
        "database": DATABASE_TYPE,
        "node_name": "Proxmox",
        "entered_by": "Script",
        "uptime_days": get_uptime_days(),
        "logical_cpus": get_logical_cpus(),
        "cpu_percent": get_cpu_percent(),
        "memory_percent": mem_percent,
        "memory_total_gb": mem_total_gb,
        "memory_used_gb": mem_used_gb,
        "disk_percent": disk_percent,
        "disk_3_type": "OS Root (/)",
        "disk_3_size_gb": disk_total_gb,
        "disk_3_used_gb": disk_used_gb,
        "disk_3_percent": disk_percent,
        "tablespace_percent": disk_percent,
        "connections": perf["connections"],
        "max_connections": perf["max_connections"],
        "connection_utilization_percent": perf["utilization_percentage"],
        "threads": perf["threads"],
        "slow_queries": perf["slow_queries"],
        "blocked_queries": perf["blocked_queries"],
        "long_running_queries": perf["long_running_queries"],
        "cache_hit_percent": perf["cache_hit_percent"],
        "aborted_connections": perf["aborted_connections"],
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
    print(
        f"Performance: Running={payload.get('threads', 0)} | Blocked={payload.get('blocked_queries', 0)} | "
        f"Long(>10s)={payload.get('long_running_queries', 0)} | SlowDelta={payload.get('slow_queries', 0)} | "
        f"CacheHit={payload.get('cache_hit_percent', 100.0)}% | AbortedDelta={payload.get('aborted_connections', 0)}"
    )

    send_to_tracker(payload, mode=mode, custom_url=args.url)
