import sys
import os

OEM_HOST        = "192.168.30.77"
OEM_PORT        = "1521"
OEM_SERVICE     = "ebs.nms.go.ug"
OEM_USER        = "sysman"
OEM_PASSWORD    = "F0rtEBBnms"

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

try:
    if oracledb_mode:
        conn = oracledb.connect(user=OEM_USER, password=OEM_PASSWORD, host=OEM_HOST, port=OEM_PORT, service_name=OEM_SERVICE)
    else:
        dsn = oracledb.makedsn(OEM_HOST, OEM_PORT, service_name=OEM_SERVICE)
        conn = oracledb.connect(user=OEM_USER, password=OEM_PASSWORD, dsn=dsn)
    
    cursor = conn.cursor()
    print("Connected successfully!")
    
    query = """
    SELECT DISTINCT s.value 
    FROM SYSMAN.MGMT$METRIC_DETAILS s 
    WHERE s.target_name = 'INFRA_nmsinfra.nms.go.ug' 
      AND s.metric_name = 'ha_backup_disk' 
      AND s.metric_column IN ('last_exec_db_backup_status', 'last_exec_arch_backup_status')
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    print("Found status values:")
    for r in rows:
         print(f"  Raw Status Value: '{r[0]}' (Type: {type(r[0])})")
        
    cursor.close()
    conn.close()
except Exception as e:
    print(f"Error: {e}")
