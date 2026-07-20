import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dba_tracker.settings')
django.setup()

from django.db import connection

sql_commands = [
    # Move database name from status -> database, severity from database -> severity
    '''UPDATE replication_monitor
       SET `database` = `status`, severity = `database`, `status` = 'Healthy'
       WHERE `status` IN ('Oracle', 'MySQL', 'MS SQL Server', 'PostgreSQL')''',
    
    # Fix remaining rows where database has severity
    '''UPDATE replication_monitor
       SET `database` = 'Unknown'
       WHERE `database` IN ('Low', 'Medium', 'High', 'Critical')
         AND `status` IN ('Healthy', 'Failed')''',
    
    # Fix old statuses
    '''UPDATE replication_monitor SET `status` = 'Healthy' WHERE `status` = 'Running'''',
    '''UPDATE replication_monitor SET `status` = 'Failed' WHERE `status` IN ('Stopped', 'Error')''',
    
    # Recreate view
    '''CREATE OR REPLACE VIEW vw_replication_monitor AS
       SELECT
           date AS `Check Date`,
           master AS `Master`,
           replica AS `Replica`,
           io_thread AS `IO Thread`,
           sql_thread AS `SQL Thread`,
           seconds_behind AS `Seconds Behind`,
           last_error AS `Last Error`,
           `status` AS `Stored Status`,
           `database` AS `Database`,
           severity AS `Severity`
       FROM replication_monitor
       ORDER BY date DESC'''
]

print("Applying data fixes and updating vw_replication_monitor...")
with connection.cursor() as c:
    for sql in sql_commands:
        try:
            c.execute(sql)
            print("Successfully executed:", sql.split('\n')[0].strip(), "...")
        except Exception as e:
            print("Error executing:", sql.split('\n')[0].strip(), "...")
            print(e)
            
print("Done!")
