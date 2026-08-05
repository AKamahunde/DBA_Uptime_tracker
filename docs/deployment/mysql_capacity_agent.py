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

def get_disk_metrics():
    """Extracts disk utilization percent and available space in GB for the root partition using df."""
    try:
        output = subprocess.check_output("df -m / | awk 'NR==2 {print $2, $4, $5}'", shell=True).decode('utf-8').strip()
        parts = output.split()
        if len(parts) == 3:
            total_mb = float(parts[0])
            avail_mb = float(parts[1])
            percent = float(parts[2].replace('%', ''))
            return percent, round(avail_mb / 1024, 2)
    except Exception as e:
        print(f"Error getting disk metrics: {e}")
    return 20.0, 0.0


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
    """Extracts the name and size of the largest table in the database."""
    try:
        query = "SELECT table_name, (data_length + index_length) FROM information_schema.tables ORDER BY (data_length + index_length) DESC LIMIT 1;"
        cmd = f"mysql --defaults-extra-file={MYSQL_CNF_FILE} -e \"{query}\" -B -N"
        output = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
        if output:
            parts = output.split('\t')
            if len(parts) == 2:
                name = parts[0]
                try:
                    bytes_val = float(parts[1])
                    gb = bytes_val / (1024 * 1024 * 1024)
                    if gb >= 0.1:
                        size_str = f"{round(gb, 2)} GB"
                    else:
                        mb = bytes_val / (1024 * 1024)
                        size_str = f"{round(mb, 2)} MB"
                    return f"{name} ({size_str})"
                except ValueError:
                    return parts[0]
            return output
        return "Unknown"
    except Exception as e:
        print(f"Error getting largest table: {e}")
        return "Unknown"

def main():
    print(f"Collecting capacity tracking metrics for {SERVER_NAME}...")
    
    # 1. OS Metrics (Root drive where MySQL is installed)
    disk_percent, avail_gb = get_disk_metrics()
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
        "available_capacity_gb": avail_gb,
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
