import subprocess
import json
import urllib.request
import urllib.error

# Configuration
UPTIME_TRACKER_API_URL = "http://192.168.35.38:90/api/capacity/ingest/"
API_KEY = "uptimetracker_api_9f2c1d8e45x9"
SERVER_NAME = "nickledbnew"
DATABASE_TYPE = "MySQL"
MYSQL_CNF_FILE = "~/.my.cnf"

def get_disk_percent():
    """Extracts disk utilization for the root partition using df."""
    try:
        output = subprocess.check_output("df -h / | awk 'NR==2 {print $5}'", shell=True).decode('utf-8').strip()
        return float(output.replace('%', ''))
    except Exception as e:
        print(f"Error getting disk percent: {e}")
        return 0.0

def get_database_size_gb():
    """Extracts total database size in GB."""
    try:
        query = "SELECT SUM(data_length + index_length)/1024/1024/1024 FROM information_schema.tables;"
        cmd = f"mysql --defaults-extra-file={MYSQL_CNF_FILE} -e \"{query}\" -B -N"
        output = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
        if output and output != "NULL":
            return float(output)
        return 0.0
    except Exception as e:
        print(f"Error getting database size: {e}")
        return 0.0

def get_largest_table():
    """Extracts the name of the largest table in the database."""
    try:
        query = "SELECT table_name FROM information_schema.tables ORDER BY (data_length + index_length) DESC LIMIT 1;"
        cmd = f"mysql --defaults-extra-file={MYSQL_CNF_FILE} -e \"{query}\" -B -N"
        output = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
        return output if output else "Unknown"
    except Exception as e:
        print(f"Error getting largest table: {e}")
        return "Unknown"

def main():
    print(f"Collecting capacity tracking metrics for {SERVER_NAME}...")
    
    # 1. OS Metrics (Root drive where MySQL is installed)
    disk_percent = get_disk_percent()
    # Assuming tablespace is on the same root drive for now
    tablespace_percent = disk_percent 
    
    # 2. Database Metrics
    db_size_gb = get_database_size_gb()
    largest_table = get_largest_table()
    
    # 3. Construct Payload
    payload = {
        "database": DATABASE_TYPE,
        "database_size_gb": round(db_size_gb, 2),
        "disk_percent": disk_percent,
        "tablespace_percent": tablespace_percent,
        "largest_table": largest_table
    }
    
    print("\n--- Capacity Payload ---")
    print(json.dumps(payload, indent=2))
    print("------------------------\n")
    
    # 4. Send to API
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(UPTIME_TRACKER_API_URL, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('X-API-KEY', API_KEY)
    
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            print(f"Successfully ingested capacity data: {result}")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        print(f"HTTP Error {e.code}: {error_body}")
    except Exception as e:
        print(f"Failed to send data: {e}")

if __name__ == "__main__":
    main()
