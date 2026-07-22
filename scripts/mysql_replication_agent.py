#!/usr/bin/env python3
import subprocess
import json
import urllib.request
import urllib.error
import socket
import argparse
import sys

# Change this to the URL of your Uptime Tracker application
UPTIME_TRACKER_API_URL = "http://192.168.35.38:90/api/etl/replication/"

def get_replication_status(mysql_user=None, mysql_password=None):
    cmd = ["mysql"]
    if mysql_user and mysql_password:
        cmd.extend(["-u", mysql_user, f"-p{mysql_password}"])
    cmd.extend(["-e", "SHOW REPLICA STATUS\\G"])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        output = result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error executing MySQL command: {e.stderr}")
        return None
    except FileNotFoundError:
        print("MySQL client not found. Ensure 'mysql' is in your system PATH.")
        return None

    if not output.strip():
        print("No replication status found. Is this server configured as a replica?")
        return None

    status = {}
    for line in output.splitlines():
        line = line.strip()
        if ":" in line and not line.startswith("*"):
            key, val = line.split(":", 1)
            status[key.strip()] = val.strip()
    return status

def map_to_payload(status_dict):
    try:
        sb_val = status_dict.get('Seconds_Behind_Source') or status_dict.get('Seconds_Behind_Master')
        if sb_val is None or str(sb_val).strip().upper() == 'NULL':
            seconds_behind = None
        else:
            seconds_behind = int(sb_val)
    except (ValueError, TypeError):
        seconds_behind = None

    replica_name = socket.gethostname()

    payload = {
        "master": status_dict.get('Source_Host') or status_dict.get('Master_Host', 'Unknown'),
        "replica": replica_name,
        "io_thread": status_dict.get('Replica_IO_Running') or status_dict.get('Slave_IO_Running', 'No'),
        "sql_thread": status_dict.get('Replica_SQL_Running') or status_dict.get('Slave_SQL_Running', 'No'),
        "seconds_behind": seconds_behind,
        "last_error": status_dict.get('Last_Error') or status_dict.get('Last_SQL_Error', '')
    }
    return payload

def send_to_tracker(payload):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        UPTIME_TRACKER_API_URL, 
        data=data, 
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'}
    )

    try:
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode('utf-8')
            print(f"Success: {response.status} - {res_body}")
    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code} - {e.read().decode('utf-8')}")
    except Exception as e:
        print(f"Unknown Error: {str(e)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Uptime Tracker MySQL Replication Agent")
    parser.add_argument("-u", "--user", required=False, help="MySQL Check User")
    parser.add_argument("-p", "--password", required=False, help="MySQL Password")
    args = parser.parse_args()

    status = get_replication_status(args.user, args.password)
    
    if status:
        payload = map_to_payload(status)
        send_to_tracker(payload)
    else:
        sys.exit(1)
