import sys

try:
    import mysql.connector
except ImportError:
    print("mysql-connector-python not installed, trying PyMySQL...")
    try:
        import pymysql as mysql
        mysql.connector = mysql
    except ImportError:
        print("Neither mysql-connector-python nor PyMySQL found.")
        sys.exit(1)

DB_CONFIG = {
    'host': '192.168.35.38',
    'user': 'root',
    'password': 'Adm1n@2020',
    'database': 'dba_tracker',
    'port': 3306,
    'connect_timeout': 10
}

TABLES = [
    'incident_register',
    'replication_monitor',
    'backup_recovery',
    'server_health',
    'maintenance_register',
    'capacity_planning',
    'availability_calculator',
]

def describe_table(cur, table):
    print(f"\n{'='*60}")
    print(f"  DESCRIBE {table}")
    print(f"{'='*60}")
    cur.execute(f"DESCRIBE `{table}`")
    rows = cur.fetchall()
    for row in rows:
        print(f"  {row}")

def sample_data(cur, table, limit=3):
    print(f"\n  -- Sample data ({limit} rows):")
    try:
        cur.execute(f"SELECT * FROM `{table}` LIMIT {limit}")
        rows = cur.fetchall()
        if not rows:
            print("  (no rows)")
        for row in rows:
            print(f"  {row}")
    except Exception as e:
        print(f"  ERROR: {e}")

def show_views(cur):
    print(f"\n{'='*60}")
    print("  EXISTING VIEWS")
    print(f"{'='*60}")
    cur.execute("SHOW FULL TABLES WHERE TABLE_TYPE = 'VIEW'")
    for row in cur.fetchall():
        print(f"  {row}")

def show_grants(cur):
    print(f"\n{'='*60}")
    print("  GRANTS FOR excel_user")
    print(f"{'='*60}")
    try:
        cur.execute("SHOW GRANTS FOR 'excel_user'@'%'")
        for row in cur.fetchall():
            print(f"  {row}")
    except Exception as e:
        print(f"  ERROR (user may not exist yet): {e}")

def main():
    print("Connecting to MySQL...")
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        print("Connected successfully!\n")
    except Exception as e:
        print(f"Connection FAILED: {e}")
        sys.exit(1)

    cur = conn.cursor()

    print("\n=== ALL TABLES ===")
    cur.execute("SHOW TABLES")
    for t in cur.fetchall():
        print(f"  {t}")

    for table in TABLES:
        try:
            describe_table(cur, table)
            sample_data(cur, table)
        except Exception as e:
            print(f"\n  ERROR describing {table}: {e}")

    show_views(cur)
    show_grants(cur)

    cur.close()
    conn.close()
    print("\nDone.")

if __name__ == '__main__':
    main()
