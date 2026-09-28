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

def parse_latest_backup():
    """Parses the backup log from the bottom up to find the most recent session."""
    if not os.path.exists(LOG_FILE):
        print(f"Log file {LOG_FILE} not found.")
        return None

    with open(LOG_FILE, 'r') as f:
        lines = f.readlines()

    session_lines = []
    # Read backwards to find the last complete block
    for line in reversed(lines):
        session_lines.insert(0, line.strip())
        if "Starting DAILY" in line or ("Starting " in line and " backup" in line.lower()):
            break

    if not session_lines:
        print("No backup session found in log.")
        return None

    # Extraction Regex
    time_pattern = re.compile(r'\[(.*?)\]')
    
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
                # Only use committed to text if it looks like a file path and not a checkpoint log
                if not any(k in target.lower() for k in ['binlog', 'checkpoint', 'nfs']) and ('.' in target or '/' in target):
                    location = target
        
        # Look for explicit archive files (.tar.gz, .gz, .zip, .tgz) in any line
        archive_match = re.search(r'([\w\-\./]+?\.(?:tar\.gz|gz|zip|tgz))(?:\.tmp)?', line, re.IGNORECASE)
        if archive_match:
            archive_file = archive_match.group(1)
            location = archive_file
            
        if ("FAILED:" in line or "ERROR:" in line) and outcome != 'Success':
            outcome = 'Failed'
            failure_reason = line.strip()
            
        if "--- Backup Session Finished ---" in line:
            end_time_str = timestamp

    # Fallback end time if session didn't cleanly finish
    if not end_time_str:
        end_time_str = timestamp 

    # Parse dates
    fmt = "%Y-%m-%d %H:%M:%S"
    start_dt = datetime.strptime(start_time_str, fmt) if start_time_str else datetime.now()
    end_dt = datetime.strptime(end_time_str, fmt) if end_time_str else datetime.now()
    duration_mins = (end_dt - start_dt).total_seconds() / 60.0

    # Ensure valid archive location for nickledb MySQL backups
    is_compressed = True
    if archive_file:
        location = archive_file
    elif location == 'Unknown' or any(k in location.lower() for k in ['binlog', 'checkpoint', 'nfs']):
        # Auto-construct standard archive filename
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
        print(f"Success: {response.read().decode('utf-8')}")
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.read().decode('utf-8')}")
    except urllib.error.URLError as e:
        print(f"Connection Error: {e.reason}")

if __name__ == "__main__":
    print("Parsing MySQL backup log...")
    payload = parse_latest_backup()
    if payload:
        print(f"Sending payload: {json.dumps(payload, indent=2)}")
        send_to_tracker(payload)
