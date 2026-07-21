#!/usr/bin/env python3
import subprocess
import json
import urllib.request
import urllib.error
import socket
import argparse
import sys

# Configuration
# Change this to the URL of your Atlas application
ATLAS_API_URL = "http://localhost:8000/api/etl/replication/"

def get_replication_status(mysql_user, mysql_password):
    """Executes SHOW REPLICA STATUS and parses the output."""
    cmd = [
        "mysql",
        "-u", mysql_user,
        f"-p{mysql_password}",
        "-e", "SHOW REPLICA STATUS\\G"
    ]
    
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

    # Parse the output into a dictionary
    status = {}
    for line in output.splitlines():
        line = line.strip()
        if ":" in line and not line.startswith("*"):
            key, val = line.split(":", 1)
            status[key.strip()] = val.strip()

    return status

def map_to_atlas_payload(status_dict):
    """Maps the raw MySQL output to the Atlas JSON payload format."""
    try:
        seconds_behind = int(status_dict.get('Seconds_Behind_Source', 0))
    except (ValueError, TypeError):
        seconds_behind = 0

    # Get the local hostname for the replica name
    replica_name = socket.gethostname()

    payload = {
        "master": status_dict.get('Source_Host', 'Unknown'),
        "replica": replica_name,
        "io_thread": status_dict.get('Replica_IO_Running', 'No'),
        "sql_thread": status_dict.get('Replica_SQL_Running', 'No'),
        "seconds_behind": seconds_behind,
        "last_error": status_dict.get('Last_Error', '') or status_dict.get('Last_SQL_Error', '')
    }
    return payload

def send_to_atlas(payload):
    """Sends the JSON payload to the Atlas API endpoint."""
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        ATLAS_API_URL, 
        data=data, 
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'}
    )

    try:
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode('utf-8')
            print(f"Success: {response.status} - {res_body}")
    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code} - {e.read().decode('utf-8')}")
    except urllib.error.URLError as e:
        print(f"Connection Error: {e.reason}")
    except Exception as e:
        print(f"Unknown Error: {str(e)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Atlas MySQL Replication Agent")
    parser.add_argument("-u", "--user", required=True, help="MySQL Replication Check User")
    parser.add_argument("-p", "--password", required=True, help="MySQL Password")
    args = parser.parse_args()

    print("Fetching MySQL replication status...")
    status = get_replication_status(args.user, args.password)
    
    if status:
        payload = map_to_atlas_payload(status)
        print(f"Sending payload to Atlas: {json.dumps(payload, indent=2)}")
        send_to_atlas(payload)
    else:
        sys.exit(1)
