"""
run_views.py  —  Connect to dba_tracker, inspect schema, then apply
                  the improved views from create_views_and_permissions.sql
Run:  python run_views.py
  or: .\\venv\\Scripts\\python.exe run_views.py   (from the backend/ dir)
"""
import sys, os

# ── Locate the SQL script relative to this file ──────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SQL_FILE   = os.path.join(SCRIPT_DIR, 'create_views_and_permissions.sql')

DB = dict(host='192.168.35.38', user='root',
          password='Adm1n@2020', database='dba_tracker', port=3306)

# ── Try to import a MySQL driver ─────────────────────────────────────────────
try:
    import mysql.connector as mc
    def connect():
        return mc.connect(**DB)
    def run(conn, sql):
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall() if cur.description else None
        cur.close()
        return rows
except ImportError:
    try:
        import pymysql
        def connect():
            return pymysql.connect(**DB, cursorclass=pymysql.cursors.Cursor)
        def run(conn, sql):
            with conn.cursor() as cur:
                cur.execute(sql)
                return cur.fetchall() if cur.description else None
    except ImportError:
        sys.exit("Install mysql-connector-python or pymysql first:\n"
                 "  pip install mysql-connector-python")

SEP = '─' * 64

def describe(conn, table):
    print(f'\n{SEP}\nDESCRIBE {table}\n{SEP}')
    rows = run(conn, f'DESCRIBE `{table}`')
    if rows:
        for r in rows:
            print('  ', r)
    else:
        print('  (table not found)')

def sample(conn, table, n=3):
    print(f'\n  Sample ({n} rows):')
    try:
        rows = run(conn, f'SELECT * FROM `{table}` LIMIT {n}')
        if not rows:
            print('  (empty)')
        for r in (rows or []):
            print('  ', r)
    except Exception as e:
        print(f'  ERROR: {e}')

def apply_views(conn):
    print(f'\n{SEP}\nAPPLYING VIEWS from {SQL_FILE}\n{SEP}')
    with open(SQL_FILE, encoding='utf-8') as f:
        raw = f.read()

    # Strip comments and split on semicolons
    import re
    # Remove block comments
    raw = re.sub(r'/\*.*?\*/', '', raw, flags=re.DOTALL)
    # Remove line comments
    raw = re.sub(r'--[^\n]*', '', raw)
    statements = [s.strip() for s in raw.split(';') if s.strip()]

    ok = err = 0
    for stmt in statements:
        first = stmt.split()[0].upper() if stmt.split() else ''
        preview = stmt[:80].replace('\n', ' ')
        try:
            run(conn, stmt)
            print(f'  ✓  {preview}...')
            ok += 1
        except Exception as e:
            print(f'  ✗  {preview}...\n     ERROR: {e}')
            err += 1
    print(f'\n  Done — {ok} OK, {err} errors.')

def check_views(conn):
    print(f'\n{SEP}\nVIEWS IN dba_tracker\n{SEP}')
    rows = run(conn, "SHOW FULL TABLES WHERE TABLE_TYPE = 'VIEW'")
    for r in (rows or []):
        print('  ', r)

def check_grants(conn):
    print(f'\n{SEP}\nGRANTS FOR excel_user\n{SEP}')
    try:
        rows = run(conn, "SHOW GRANTS FOR 'excel_user'@'%'")
        for r in (rows or []):
            print('  ', r)
    except Exception as e:
        print(f'  (excel_user may not exist yet): {e}')

TABLES = [
    'incident_register', 'replication_monitor', 'backup_recovery',
    'server_health', 'maintenance_register', 'capacity_planning',
    'availability_calculator',
]

def main():
    print('Connecting to MySQL …')
    try:
        conn = connect()
        print('Connected!\n')
    except Exception as e:
        sys.exit(f'Connection FAILED: {e}')

    print(f'{SEP}\nALL TABLES\n{SEP}')
    for r in (run(conn, 'SHOW TABLES') or []):
        print(' ', r)

    for t in TABLES:
        describe(conn, t)
        sample(conn, t)

    check_views(conn)
    apply_views(conn)
    check_views(conn)
    check_grants(conn)
    conn.close()
    print('\nAll done.')

if __name__ == '__main__':
    main()
