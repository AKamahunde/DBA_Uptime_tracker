#!/usr/bin/env python3
"""
MySQL Journal Incident Monitor
Version: 1.0.0
Description: Scans the systemd journal for mysql.service shutdowns and crashes,
             calculates the exact downtime, and POSTs the incident to the DBA Hub API.
"""

import os
import sys
import re
import json
import subprocess
import urllib.request
from datetime import datetime

def parse_journal_timestamp(line):
    """
    Parses timestamps from systemd journal output line.
    Example line: 'Jul 28 06:45:12 nickledbnew systemd[1]: Stopping mysql.service...'
    """
    try:
        # The first 15 characters represent 'Mmm dd hh:mm:ss'
        ts_str = line[:15]
        current_year = datetime.now().year
        # Combine with current year as journalctl doesn't print the year by default
        dt = datetime.strptime(f"{current_year} {ts_str}", "%Y %b %d %H:%M:%S")
        return dt
    except Exception:
        return None

def main():
    api_url = "http://localhost:8000/api/incident/ingest/"  # Adjust as needed or use arg
    api_key = ""  # Will be read from environment or arguments
    timeframe = "24 hours ago"
    db_type = "MySQL"
    system_name = "MySQL Instance"
    
    # CLI Overrides
    if len(sys.argv) > 1:
        api_url = sys.argv[1]
    if len(sys.argv) > 2:
        api_key = sys.argv[2]
    if len(sys.argv) > 3:
        timeframe = sys.argv[3]
        
    if not api_key:
        api_key = os.environ.get('REPLICATION_API_KEY', '')
        
    if not api_key:
        print("Error: Ingestion API Key must be provided as an argument or in REPLICATION_API_KEY env var.", file=sys.stderr)
        sys.exit(1)

    print(f"Running systemd journal check for MySQL (lookback: {timeframe})...")
    
    # Fetch journalctl logs for the mysql service
    try:
        result = subprocess.run(
            ["journalctl", "-u", "mysql", "--since", timeframe, "--no-pager"],
            capture_output=True,
            text=True,
            check=True
        )
    except Exception as e:
        print(f"Error executing journalctl: {e}", file=sys.stderr)
        print("Make sure you run this script as root/sudo.", file=sys.stderr)
        sys.exit(1)
        
    lines = result.stdout.splitlines()
    
    outage_start_dt = None
    outage_type = "Restart"
    log_snippet = []
    
    incidents_detected = []
    
    for line in lines:
        if not line.strip():
            continue
            
        dt = parse_journal_timestamp(line)
        if not dt:
            continue
            
        # Detect Outage Starts:
        # A) Clean Shutdown Init
        if "Stopping mysql.service" in line:
            if not outage_start_dt:
                outage_start_dt = dt
                outage_type = "Graceful Restart"
            log_snippet.append(line)
            
        # B) Crash exits
        elif "Main process exited" in line or "Failed with result" in line:
            if not outage_start_dt:
                outage_start_dt = dt
                outage_type = "Crash Outage"
            log_snippet.append(line)
            
        # Detect Outage Ends:
        # C) Service Started up
        elif "Started mysql.service" in line:
            if outage_start_dt:
                log_snippet.append(line)
                
                # We have a matched outage window!
                incidents_detected.append({
                    "start": outage_start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "end": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "database": db_type,
                    "system": system_name,
                    "root_cause": f"AUTO-LOGGED: MySQL restart ({outage_type}) detected. Logs:\n" + "\n".join(log_snippet)
                })
                
                # Reset tracking
                outage_start_dt = None
                log_snippet = []
        
        # Accumulate context lines if we are inside an outage window
        elif outage_start_dt:
            log_snippet.append(line)
            
    if not incidents_detected:
        print("No outages detected in journalctl logs for the last 24 hours.")
        return
        
    print(f"Detected {len(incidents_detected)} outages. Ingesting to API...")
    
    success_count = 0
    for payload in incidents_detected:
        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                api_url,
                data=data,
                headers={
                    'Content-Type': 'application/json',
                    'X-API-KEY': api_key
                },
                method='POST'
            )
            
            with urllib.request.urlopen(req, timeout=10) as response:
                res_body = json.loads(response.read().decode('utf-8'))
                if res_body.get('status') == 'success':
                    print(f"Logged Incident: {res_body.get('ticket')} (Duration: {payload['start']} to {payload['end']})")
                    success_count += 1
                elif res_body.get('status') == 'ignored':
                    print(f"Ignored: Already registered ({payload['start']})")
                    success_count += 1
        except Exception as e:
            print(f"Failed to ingest incident starting at {payload['start']}: {e}", file=sys.stderr)
            
    print(f"Completed. Successfully registered/verified {success_count} incidents.")

if __name__ == '__main__':
    main()
