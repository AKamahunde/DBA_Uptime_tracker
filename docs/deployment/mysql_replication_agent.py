#!/usr/bin/env python3
import argparse
import subprocess
import json
import urllib.request
import urllib.error
import sys

# Configuration
# Replace with the actual URL/IP of your Uptime Tracker server
UPTIME_TRACKER_API_URL = "http://192.168.35.38:90/api/replication/ingest/"
API_KEY = "YOUR-GENERATED-API-KEY-HERE"

def get_replication_status(user, password):
    """Runs SHOW REPLICA STATUS and parses the output."""
    cmd = ["mysql", "-u", user]
    if password:
        cmd.append(f"-p{password}")
    cmd.extend(["-e", "SHOW REPLICA STATUS\\G"])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running mysql command: {e.stderr}")
        sys.exit(1)
        
    output = result.stdout
    status = {}
    
    for line in output.splitlines():
        line = line.strip()
        if ":" in line:
            key, val = line.split(":", 1)
            status[key.strip()] = val.strip()
            
    if not status:
        print("No replica status found. Is this a replica server?")
        sys.exit(1)
        
    return status

def send_to_tracker(payload):
    """Sends the JSON payload to the Uptime Tracker API."""
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(UPTIME_TRACKER_API_URL, data=data)
    req.add_header('Content-Type', 'application/json')
    req.add_header('X-API-KEY', API_KEY)
    
    try:
        response = urllib.request.urlopen(req)
        resp_data = response.read()
        print(f"Success: {resp_data.decode('utf-8')}")
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.read().decode('utf-8')}")
    except urllib.error.URLError as e:
        print(f"Connection Error: {e.reason}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MySQL Replication Monitoring Agent")
    parser.add_argument("-u", "--user", required=True, help="MySQL user")
    parser.add_argument("-p", "--password", required=False, default="", help="MySQL password")
    
    args = parser.parse_args()
    
    # Fetch status
    status = get_replication_status(args.user, args.password)
    
    # Format payload for Uptime Tracker
    seconds_behind = status.get('Seconds_Behind_Master', '0')
    if seconds_behind == 'NULL':
        seconds_behind = None
    else:
        seconds_behind = int(seconds_behind)
        
    payload = {
        "master": status.get('Source_Host', 'Unknown'),
        "replica": "nickeldbdr",  # Hardcoded or dynamically fetch hostname
        "io_thread": status.get('Replica_IO_Running', 'No'),
        "sql_thread": status.get('Replica_SQL_Running', 'No'),
        "seconds_behind": seconds_behind,
        "last_error": status.get('Last_Error', '')
    }
    
    print(f"Sending payload to Uptime Tracker: {json.dumps(payload, indent=2)}")
    send_to_tracker(payload)
