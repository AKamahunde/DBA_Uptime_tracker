#!/usr/bin/env python3
"""
MySQL Log Incident Analyzer
Version: 1.0.0
Description: Parses the MySQL error log to identify server downtime, crashes, 
             and graceful restarts, calculating the exact outage window for July 2026.
"""

import os
import sys
import re
from datetime import datetime, timedelta

def parse_mysql_timestamp(ts_str):
    """
    Parses various MySQL error log timestamp formats.
    Format 1: ISO 8601 (MySQL 5.7+): 2026-07-30T10:15:30.123456Z
    Format 2: Classic: 2026-07-30 10:15:30
    Format 3: Two-digit year: 260730 10:15:30
    """
    ts_str = ts_str.strip()
    try:
        if 'T' in ts_str:
            # Strip microseconds/timezone if present
            clean_ts = ts_str.split('.')[0].split('Z')[0]
            return datetime.strptime(clean_ts, "%Y-%m-%dT%H:%M:%S")
        elif len(ts_str.split()[0]) == 6:
            return datetime.strptime(ts_str, "%y%m%d %H:%M:%S")
        else:
            return datetime.strptime(ts_str.split('.')[0], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

def main():
    log_path = "/var/log/mysql/error.log"
    if len(sys.argv) > 1:
        log_path = sys.argv[1]
        
    if not os.path.exists(log_path):
        print(f"Error: MySQL error log not found at {log_path}", file=sys.stderr)
        print("Please check paths or pass the log path as an argument.", file=sys.stderr)
        sys.exit(1)
        
    print(f"Analyzing MySQL log: {log_path}")
    print("Scanning for July 2026 incidents...\n")
    
    # Common regex patterns to match timestamps
    # Matches: 2026-07-30T10:15:30.123Z  or  260730 10:15:30  or  2026-07-30 10:15:30
    ts_pattern = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z)?|\d{6} \d{2}:\d{2}:\d{2}|\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
    
    events = []
    
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                match = ts_pattern.match(line)
                if not match:
                    continue
                    
                ts_str = match.group(1)
                dt = parse_mysql_timestamp(ts_str)
                if not dt:
                    continue
                    
                # We are looking for July 2026
                if dt.year != 2026 or dt.month != 7:
                    continue
                    
                # Look for shutdown signals
                if "Initiating shutdown" in line or "Received SHUTDOWN from user" in line or "Stopping" in line:
                    events.append({'type': 'SHUTDOWN', 'time': dt, 'line': line.strip()})
                
                # Look for startup signals
                elif "ready for connections" in line or "Server socket created on IP" in line or "Ready for start" in line:
                    events.append({'type': 'STARTUP', 'time': dt, 'line': line.strip()})
                    
                # Look for crash indicators
                elif "Assertion failed" in line or "Segmentation fault" in line or "InnoDB: Fatal error" in line:
                    events.append({'type': 'CRASH', 'time': dt, 'line': line.strip()})
                    
                # Keep track of last logged line timestamp to approximate crash time
                else:
                    # Storing a heartbeat of normal execution
                    if not events or events[-1]['type'] != 'HEARTBEAT' or (dt - events[-1]['time']).seconds > 300:
                        events.append({'type': 'HEARTBEAT', 'time': dt})
                        
    except PermissionError:
        print(f"Error: Permission denied reading {log_path}.", file=sys.stderr)
        print("Please run this script with sudo.", file=sys.stderr)
        sys.exit(1)

    # Process events to identify downtime intervals
    incidents = []
    last_shutdown = None
    last_heartbeat = None
    
    for i, event in enumerate(events):
        ev_type = event['type']
        ev_time = event['time']
        
        if ev_type == 'SHUTDOWN':
            last_shutdown = ev_time
        elif ev_type == 'HEARTBEAT':
            last_heartbeat = ev_time
        elif ev_type == 'CRASH':
            # Approximate crash time
            last_shutdown = ev_time
        elif ev_type == 'STARTUP':
            if last_shutdown:
                # We had a recorded shutdown/crash
                duration = ev_time - last_shutdown
                incidents.append({
                    'start': last_shutdown,
                    'end': ev_time,
                    'duration': duration,
                    'type': 'Graceful Restart' if 'SHUTDOWN' in [events[j]['type'] for j in range(max(0, i-5), i)] else 'Crash Outage'
                })
                last_shutdown = None
            elif last_heartbeat and (ev_time - last_heartbeat) > timedelta(minutes=5):
                # Server started up after a gap but no clean shutdown was logged (Implies crash/reboot)
                duration = ev_time - last_heartbeat
                incidents.append({
                    'start': last_heartbeat,
                    'end': ev_time,
                    'duration': duration,
                    'type': 'Crash / Sudden Stop'
                })
            last_heartbeat = ev_time

    # Output results
    if not incidents:
        print("No MySQL downtime incidents detected in July 2026.")
        return
        
    print(f"{'OUTAGE START':<20} | {'RECOVERY END':<20} | {'DURATION':<15} | {'INCIDENT TYPE'}")
    print("-" * 75)
    for inc in incidents:
        dur_str = str(inc['duration']).split('.')[0] # Remove microsecond remainder
        print(f"{inc['start'].strftime('%Y-%m-%d %H:%M:%S'):<20} | "
              f"{inc['end'].strftime('%Y-%m-%d %H:%M:%S'):<20} | "
              f"{dur_str:<15} | "
              f"{inc['type']}")
              
    print("\nNote: Outage calculations are based on timestamps logged in the file.")
    print("Crash detections use logs where a STARTUP exists without a preceding SHUTDOWN.")

if __name__ == "__main__":
    main()
