#!/usr/bin/env python3
"""
Linux Server Health Agent
Version: 1.0.0
Description: Zero-dependency Python script to collect CPU, RAM, and Disk metrics 
             on Linux servers and transmit them to the DBA operations tracker API.
"""

import os
import sys
import time
import socket
import json
import argparse
import urllib.request

def get_cpu_percent():
    """Calculates average CPU usage over a 1-second interval by reading /proc/stat."""
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline()
        parts = line.split()
        # cpu user nice system idle iowait irq softirq ...
        user, nice, system, idle, iowait = map(float, parts[1:6])
        total_1 = user + nice + system + idle + iowait
        idle_1 = idle + iowait
        
        time.sleep(1)
        
        with open('/proc/stat', 'r') as f:
            line = f.readline()
        parts = line.split()
        user, nice, system, idle, iowait = map(float, parts[1:6])
        total_2 = user + nice + system + idle + iowait
        idle_2 = idle + iowait
        
        total_diff = total_2 - total_1
        idle_diff = idle_2 - idle_1
        if total_diff > 0:
            return round((1.0 - (idle_diff / total_diff)) * 100.0, 2)
    except Exception as e:
        print(f"Error reading CPU stats: {e}", file=sys.stderr)
    return 0.0

def get_memory_percent():
    """Calculates Memory utilization percentage by reading /proc/meminfo."""
    try:
        meminfo = {}
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                parts = line.split(':')
                if len(parts) == 2:
                    name = parts[0].strip()
                    val = int(parts[1].split()[0])
                    meminfo[name] = val
        
        total = meminfo.get('MemTotal', 0)
        free = meminfo.get('MemFree', 0)
        buffers = meminfo.get('Buffers', 0)
        cached = meminfo.get('Cached', 0)
        reclaimable = meminfo.get('SReclaimable', 0)
        
        # Fallback if MemAvailable is not direct (older kernels)
        available = meminfo.get('MemAvailable', free + buffers + cached + reclaimable)
        if total > 0:
            used = total - available
            return round((used / total) * 100.0, 2)
    except Exception as e:
        print(f"Error reading Memory stats: {e}", file=sys.stderr)
    return 0.0

def get_disk_percent(path):
    """Calculates disk usage percentage for a specified path using statvfs."""
    try:
        stat = os.statvfs(path)
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bavail * stat.f_frsize
        used = total - free
        if total > 0:
            return round((used / total) * 100.0, 2)
    except Exception as e:
        print(f"Error reading Disk stats for path '{path}': {e}", file=sys.stderr)
    return 0.0

def main():
    parser = argparse.ArgumentParser(description="Linux Server Health Agent")
    parser.add_argument('--database', default='Oracle', help="Database type mapping (e.g. Oracle, MySQL, PostgreSQL)")
    parser.add_argument('--server', default=socket.gethostname(), help="Hostname of the server being monitored")
    parser.add_argument('--path', default='/', help="Filesystem path to monitor disk usage on")
    parser.add_argument('--api-url', required=True, help="Uptime Tracker server health ingest endpoint URL")
    parser.add_argument('--api-key', required=True, help="API Key for ingestion authentication")
    
    args = parser.parse_args()
    
    print("Collecting server health statistics...")
    cpu = get_cpu_percent()
    ram = get_memory_percent()
    disk = get_disk_percent(args.path)
    
    payload = {
        "database": args.database,
        "server": args.server,
        "cpu_percent": cpu,
        "memory_percent": ram,
        "disk_percent": disk,
        "tablespace_percent": 0.0,
        "connections": 0,
        "slow_queries": 0,
        "threads": 0
    }
    
    print(f"Server: {payload['server']}")
    print(f"Database Mapping: {payload['database']}")
    print(f"CPU: {payload['cpu_percent']}% | RAM: {payload['memory_percent']}% | Disk: {payload['disk_percent']}%")
    
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            args.api_url,
            data=data,
            headers={
                'Content-Type': 'application/json',
                'X-API-KEY': args.api_key
            },
            method='POST'
        )
        
        print(f"Sending payload to {args.api_url}...")
        with urllib.request.urlopen(req, timeout=10) as response:
            res_body = response.read().decode('utf-8')
            print("Response:", res_body)
            print("Success! Health metrics ingested.")
    except Exception as e:
        print(f"HTTP Request failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
