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
API_KEY = "uptimetracker_api_9f2c1d8e45x9"

def run_mysql_cmd(query, user=None, password=None):
    """Helper to run a mysql query and return parsed key-value dict."""
    cmd = ["mysql"]
    if user:
        cmd.extend(["-u", user])
    if password:
        cmd.append(f"-p{password}")
    cmd.extend(["-e", query])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        output = result.stdout
        status = {}
        for line in output.splitlines():
            line = line.strip()
            if ":" in line:
                key, val = line.split(":", 1)
                status[key.strip()] = val.strip()
        return status
    except subprocess.CalledProcessError:
        return {}

def get_replication_status(user=None, password=None):
    """Runs SHOW REPLICA STATUS or fallbacks to SHOW SLAVE STATUS and parses the output."""
    # 1. Try SHOW REPLICA STATUS (MySQL 8.0.22+)
    status = run_mysql_cmd("SHOW REPLICA STATUS\\G", user, password)
    
    # 2. Fallback to SHOW SLAVE STATUS (MySQL < 8.0.22 and MariaDB)
    if not status:
        print("SHOW REPLICA STATUS returned no data or failed. Falling back to SHOW SLAVE STATUS...")
        status = run_mysql_cmd("SHOW SLAVE STATUS\\G", user, password)
        
    if not status:
        print("Error: No replication status found. Is this a replication replica server?")
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
    parser.add_argument("-u", "--user", required=False, default=None, help="MySQL user (uses .my.cnf if not provided)")
    parser.add_argument("-p", "--password", required=False, default="", help="MySQL password")
    
    args = parser.parse_args()
    
    # Fetch status
    status = get_replication_status(args.user, args.password)
    
    # Fetch Seconds Behind
    seconds_behind = status.get('Seconds_Behind_Master')
    if seconds_behind == 'NULL' or seconds_behind is None:
        seconds_behind = None
    else:
        try:
            seconds_behind = int(seconds_behind)
        except ValueError:
            seconds_behind = None
            
    # Support both inclusive naming (Source/Replica) and legacy naming (Master/Slave)
    master_host = status.get('Source_Host') or status.get('Master_Host') or 'Unknown'
    io_running = status.get('Replica_IO_Running') or status.get('Slave_IO_Running') or 'No'
    sql_running = status.get('Replica_SQL_Running') or status.get('Slave_SQL_Running') or 'No'
    last_error = status.get('Last_Error') or status.get('Last_SQL_Error') or status.get('Last_IO_Error') or ''
        
    payload = {
        "master": master_host,
        "replica": "nickeldbdr",  # Hardcoded or dynamically fetch hostname
        "io_thread": io_running,
        "sql_thread": sql_running,
        "seconds_behind": seconds_behind,
        "last_error": last_error
    }
    
    print(f"Sending payload to Uptime Tracker: {json.dumps(payload, indent=2)}")
    send_to_tracker(payload)
