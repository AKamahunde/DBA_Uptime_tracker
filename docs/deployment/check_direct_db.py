import sys
import os

DB_HOST        = "nmsinfra.nms.go.ug"
DB_PORT        = 1530
DB_SID         = "INFRA"
DB_PASS        = "F0rtEBBnms"

oracledb_mode = True
try:
    import oracledb
except ImportError:
    try:
        import cx_Oracle as oracledb
        oracledb_mode = False
    except ImportError:
        print("Error: The 'oracledb' or 'cx_Oracle' library is required.")
        exit(1)

users_to_try = ['system', 'dbsnmp']

for user in users_to_try:
    print(f"\nTrying connection as user '{user}' on {DB_HOST}:{DB_PORT} ({DB_SID})...")
    try:
        if oracledb_mode:
            conn = oracledb.connect(user=user, password=DB_PASS, host=DB_HOST, port=DB_PORT, sid=DB_SID)
        else:
            dsn = oracledb.makedsn(DB_HOST, DB_PORT, sid=DB_SID)
            conn = oracledb.connect(user=user, password=DB_PASS, dsn=dsn)
            
        cursor = conn.cursor()
        print(f"Connection as '{user}' SUCCESSFUL!")
        
        # Test Query RMAN Backup Details
        query = """
        SELECT start_time, input_type, status, elapsed_seconds, output_bytes
        FROM v$rman_backup_job_details
        WHERE start_time >= SYSDATE - 10
        ORDER BY start_time DESC
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        print(f"Found {len(rows)} recent backups directly from database:")
        for r in rows[:5]:
            print(f"  Date: {r[0]} | Type: {r[1]} | Status: {r[2]} | Duration (sec): {r[3]} | Size: {r[4]}")
            
        cursor.close()
        conn.close()
        break
    except Exception as e:
        print(f"Connection as '{user}' FAILED: {e}")
