#!/usr/bin/env python3
import os
import re
import json
import urllib.request
import urllib.error
from datetime import datetime

# Configuration
LOG_FILE = "/var/log/mysql_backup.log"
UPTIME_TRACKER_API_URL = "http://192.168.35.38:90/api/backup/ingest/"
API_KEY = "uptimetracker_api_9f2c1d8e45x9"
DATABASE_INSTANCE = "nickledbnew"

def parse_all_backups():
    """Parses the backup log from top to bottom, finding all sessions for the current month."""
    if not os.path.exists(LOG_FILE):
        print(f"Log file {LOG_FILE} not found.")
        return []

    with open(LOG_FILE, 'r') as f:
        lines = f.readlines()

    all_payloads = []
    current_session_lines = []
    in_session = False
    
    current_month = datetime.now().month
    current_year = datetime.now().year

    # Extraction Regex
    time_pattern = re.compile(r'\[(.*?)\]')

    for line in lines:
        if "Starting " in line and " backup" in line.lower():
            in_session = True
            current_session_lines = [line.strip()]
        elif in_session:
            current_session_lines.append(line.strip())
            if "--- Backup Session Finished ---" in line:
                # We reached the end of a session block, let's parse it!
                payload = parse_session_block(current_session_lines, time_pattern)
                if payload:
                    # Check if it belongs to the current month/year
                    start_dt = datetime.fromisoformat(payload["start"])
                    if start_dt.month == current_month and start_dt.year == current_year:
                        all_payloads.append(payload)
                
                # Reset
                in_session = False
                current_session_lines = []

    return all_payloads

def parse_session_block(session_lines, time_pattern):
    start_time_str = None
    end_time_str = None
    backup_type = 'Incremental'
    outcome = 'Failed'
    location = 'Unknown'
    failure_reason = ''
    archive_file = None
    
    for line in session_lines:
        match = time_pattern.search(line)
        if not match:
            continue
            
        timestamp = match.group(1)
        
        if "Starting " in line and " backup" in line.lower():
            start_time_str = timestamp
            if "FULL" in line.upper():
                backup_type = 'Full'
            else:
                backup_type = 'Incremental'
                
        if "SUCCESS" in line.upper():
            outcome = 'Success'
            if "committed to" in line:
                target = line.split("committed to")[-1].strip()
                if not any(k in target.lower() for k in ['binlog', 'checkpoint', 'nfs']) and ('.' in target or '/' in target):
                    location = target
        
        archive_match = re.search(r'([\w\-\./]+?\.(?:tar\.gz|gz|zip|tgz))(?:\.tmp)?', line, re.IGNORECASE)
        if archive_match:
            archive_file = archive_match.group(1)
            location = archive_file
            
        if ("FAILED:" in line or "ERROR:" in line) and outcome != 'Success':
            outcome = 'Failed'
            failure_reason = line.strip()
            
        if "--- Backup Session Finished ---" in line:
            end_time_str = timestamp

    if not start_time_str:
        return None
        
    if not end_time_str:
        end_time_str = timestamp 

    # Parse dates
    fmt = "%Y-%m-%d %H:%M:%S"
    start_dt = datetime.strptime(start_time_str, fmt) if start_time_str else datetime.now()
    end_dt = datetime.strptime(end_time_str, fmt) if end_time_str else datetime.now()
    duration_mins = (end_dt - start_dt).total_seconds() / 60.0

    is_compressed = True
    if archive_file:
        location = archive_file
    elif location == 'Unknown' or any(k in location.lower() for k in ['binlog', 'checkpoint', 'nfs']):
        location = f"{DATABASE_INSTANCE}_{start_dt.strftime('%Y-%m-%d_%H-%M-%S')}_{backup_type.lower()}.tar.gz"

    return {
        "database_instance": DATABASE_INSTANCE,
        "backup_type": backup_type,
        "backup_used": os.path.basename(location) if location != 'Unknown' else 'N/A',
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "duration_mins": round(duration_mins, 4),
        "outcome": outcome,
        "failure_reason": failure_reason,
        "location": location,
        "verified": outcome == 'Success',
        "compression": is_compressed,
        "retention": "180 Days" if backup_type == 'Full' else "7 Days"
    }

def send_to_tracker(payload):
    """Sends the JSON payload to the Uptime Tracker API."""
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(UPTIME_TRACKER_API_URL, data=data)
    req.add_header('Content-Type', 'application/json')
    req.add_header('X-API-KEY', API_KEY)
    
    try:
        response = urllib.request.urlopen(req)
        print(f"Success for {payload['start']}: {response.read().decode('utf-8')}")
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code} for {payload['start']}: {e.read().decode('utf-8')}")
    except urllib.error.URLError as e:
        print(f"Connection Error: {e.reason}")

if __name__ == "__main__":
    print("Parsing ALL historical MySQL backups for this month...")
    payloads = parse_all_backups()
    
    print(f"Found {len(payloads)} backups for this month! Sending them to Uptime Tracker...\n")
    for payload in payloads:
        print(f"--- Sending Payload for {payload['start']} ---")
        print(json.dumps(payload, indent=2))
        send_to_tracker(payload)
        print()
        
    print("Historical import complete.")
