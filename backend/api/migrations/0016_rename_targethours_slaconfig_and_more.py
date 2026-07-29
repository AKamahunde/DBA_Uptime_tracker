from django.db import migrations, models
import os
import re

def apply_sql_views(apps, schema_editor):
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sql_file_path = os.path.join(base_dir, '..', 'create_views_and_permissions.sql')
    if not os.path.exists(sql_file_path):
        sql_file_path = os.path.join(base_dir, 'create_views_and_permissions.sql')
    if not os.path.exists(sql_file_path):
        sql_file_path = os.path.join(os.path.dirname(base_dir), 'create_views_and_permissions.sql')

    if not os.path.exists(sql_file_path):
        raise FileNotFoundError(f"Could not find views SQL file at {sql_file_path}")

    with open(sql_file_path, encoding='utf-8') as f:
        raw_sql = f.read()

    raw_sql = re.sub(r'/\*.*?\*/', '', raw_sql, flags=re.DOTALL)
    raw_sql = re.sub(r'--[^\n]*', '', raw_sql)
    
    statements = []
    current_delimiter = ';'
    current_statement = []
    
    for line in raw_sql.splitlines():
        line_stripped = line.strip()
        if not line_stripped:
            continue
        if line_stripped.upper().startswith('DELIMITER'):
            parts = line_stripped.split()
            if len(parts) > 1:
                current_delimiter = parts[1]
            continue
        if line_stripped.endswith(current_delimiter):
            line_without_delimiter = line_stripped[:-len(current_delimiter)]
            current_statement.append(line_without_delimiter)
            stmt = '\n'.join(current_statement).strip()
            if stmt:
                statements.append(stmt)
            current_statement = []
        else:
            current_statement.append(line)
            
    stmt = '\n'.join(current_statement).strip()
    if stmt:
        statements.append(stmt)

    with schema_editor.connection.cursor() as cursor:
        for stmt in statements:
            if not stmt or stmt.upper().startswith('USE '):
                continue
            cursor.execute(stmt)

class Migration(migrations.Migration):

    dependencies = [
        ('api', '0015_ugandaholiday_alter_targethours_sla_percent'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='targethours',
            unique_together={('month', 'database')},
        ),
        migrations.RemoveField(
            model_name='targethours',
            name='database_type',
        ),
        migrations.RenameModel(
            old_name='TargetHours',
            new_name='SLAConfig',
        ),
        migrations.AlterModelTable(
            name='slaconfig',
            table='sla_config',
        ),
        migrations.AlterField(
            model_name='slaconfig',
            name='database',
            field=models.CharField(choices=[('MySQL', 'MySQL'), ('Oracle', 'Oracle'), ('MS SQL Server', 'MS SQL Server'), ('PostgreSQL', 'PostgreSQL')], max_length=50),
        ),
        migrations.RunPython(apply_sql_views, reverse_code=migrations.RunPython.noop),
    ]
