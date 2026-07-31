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
            SELECT DISTINCT backup_date, backup_type, backup_status, backup_size
            FROM (
              SELECT 
                d.value AS backup_date,
                'Full' AS backup_type,
                (SELECT value FROM (
                   SELECT value FROM SYSMAN.MGMT$METRIC_DETAILS s 
                   WHERE s.target_guid = d.target_guid 
                     AND s.metric_name = 'ha_backup_disk' 
                     AND s.metric_column = 'last_exec_db_backup_status'
                     AND s.collection_timestamp <= d.collection_timestamp
                   ORDER BY s.collection_timestamp DESC
                 ) WHERE ROWNUM = 1) AS backup_status,
                (SELECT value FROM (
                   SELECT value FROM SYSMAN.MGMT$METRIC_DETAILS sz 
                   WHERE sz.target_guid = d.target_guid 
                     AND sz.metric_name = 'ha_backup_disk' 
                     AND sz.metric_column = 'last_db_backup_size'
                     AND sz.collection_timestamp <= d.collection_timestamp
                   ORDER BY sz.collection_timestamp DESC
                 ) WHERE ROWNUM = 1) AS backup_size
              FROM SYSMAN.MGMT$METRIC_DETAILS d
              WHERE (UPPER(d.target_name) LIKE '%NMSINFRA%' OR UPPER(d.target_name) LIKE '%INFRA%')
                AND d.metric_name = 'ha_backup_disk'
                AND d.metric_column = 'last_exec_db_backup_date'
                AND d.collection_timestamp >= SYSDATE - 30
              UNION ALL
              SELECT 
                d.value AS backup_date,
                'Incremental' AS backup_type,
                (SELECT value FROM (
                   SELECT value FROM SYSMAN.MGMT$METRIC_DETAILS s 
                   WHERE s.target_guid = d.target_guid 
                     AND s.metric_name = 'ha_backup_disk' 
                     AND s.metric_column = 'last_exec_arch_backup_status'
                     AND s.collection_timestamp <= d.collection_timestamp
                   ORDER BY s.collection_timestamp DESC
                 ) WHERE ROWNUM = 1) AS backup_status,
                (SELECT value FROM (
                   SELECT value FROM SYSMAN.MGMT$METRIC_DETAILS sz 
                   WHERE sz.target_guid = d.target_guid 
                     AND sz.metric_name = 'ha_backup_disk' 
                     AND sz.metric_column = 'last_arch_backup_size'
                     AND sz.collection_timestamp <= d.collection_timestamp
                   ORDER BY sz.collection_timestamp DESC
                 ) WHERE ROWNUM = 1) AS backup_size
              FROM SYSMAN.MGMT$METRIC_DETAILS d
              WHERE (UPPER(d.target_name) LIKE '%NMSINFRA%' OR UPPER(d.target_name) LIKE '%INFRA%')
                AND d.metric_name = 'ha_backup_disk'
                AND d.metric_column = 'last_exec_arch_backup_date'
                AND d.collection_timestamp >= SYSDATE - 30
            )
            WHERE backup_date IS NOT NULL
            ORDER BY backup_date DESC
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    print(f"Query returned {len(rows)} rows:")
    for r in rows[:15]:
        print(f"Date: {r[0]} | Type: {r[1]} | Status: '{r[2]}' | Size: {r[3]}")
        
    cursor.close()
    conn.close()
except Exception as e:
    print(f"Error: {e}")
