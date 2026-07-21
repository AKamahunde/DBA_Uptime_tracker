#!/usr/bin/env python3
import os
import json
import urllib.request
import urllib.error
import subprocess
from datetime import datetime

# Configuration
UPTIME_TRACKER_API_URL = "http://192.168.35.38:90/api/health/ingest/"
API_KEY = "uptimetracker_api_9f2c1d8e45x9"
SERVER_NAME = "nickledbnew"
DATABASE_TYPE = "MySQL"

def get_cpu_percent():
    """Extracts CPU utilization using top command."""
    try:
        # Run top in batch mode, 1 iteration, get CPU line
        output = subprocess.check_output("top -bn1 | grep 'Cpu(s)'", shell=True).decode('utf-8')
        # Typical output: %Cpu(s):  5.0 us,  2.0 sy,  0.0 ni, 92.0 id,  1.0 wa,  0.0 hi,  0.0 si,  0.0 st
        # We find the 'id' (idle) value and subtract from 100
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
        # Output: /dev/sda1 100G 20G 80G 20% /
        parts = output.split()
        use_percent = parts[-2].replace('%', '')
        return round(float(use_percent), 2)
    except Exception as e:
        print(f"Error getting Disk: {e}")
    return 0.0

def get_mysql_status(variable_name):
    """Executes a MySQL query to get a specific global status variable."""
    try:
        cmd = f"mysql -u root -e \"SHOW GLOBAL STATUS LIKE '{variable_name}';\" -B -N"
        output = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
        if output:
            return int(output.split('\t')[1])
    except Exception as e:
        print(f"Error getting MySQL {variable_name}: {e}")
    return 0

def collect_health_data():
    """Collects all OS and database metrics."""
    disk_percent = get_disk_percent()
    
    return {
        "server": SERVER_NAME,
        "database": DATABASE_TYPE,
        "cpu_percent": get_cpu_percent(),
        "memory_percent": get_memory_percent(),
        "disk_percent": disk_percent,
        "tablespace_percent": disk_percent, # As requested, mirrors root disk
        "connections": get_mysql_status("Threads_connected"),
        "threads": get_mysql_status("Threads_running"),
        "slow_queries": get_mysql_status("Slow_queries")
    }

def send_to_tracker(payload):
    """Sends the JSON payload to the Uptime Tracker API."""
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(UPTIME_TRACKER_API_URL, data=data)
    req.add_header('Content-Type', 'application/json')
    req.add_header('X-API-KEY', API_KEY)
    
    try:
        response = urllib.request.urlopen(req)
        print(f"Successfully ingested health data: {response.read().decode('utf-8')}")
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.read().decode('utf-8')}")
    except urllib.error.URLError as e:
        print(f"Connection Error: {e.reason}")

if __name__ == "__main__":
    print(f"Collecting server health metrics for {SERVER_NAME}...")
    payload = collect_health_data()
    
    print("\n--- Health Payload ---")
    print(json.dumps(payload, indent=2))
    print("----------------------\n")
    
    send_to_tracker(payload)
