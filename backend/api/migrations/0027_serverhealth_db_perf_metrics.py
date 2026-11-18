from django.db import migrations, models


def add_db_perf_columns_if_not_exists(apps, schema_editor):
    connection = schema_editor.connection
    columns_to_add = [
        ("blocked_queries", "INT NOT NULL DEFAULT 0"),
        ("long_running_queries", "INT NOT NULL DEFAULT 0"),
        ("cache_hit_percent", "DECIMAL(5,2) NOT NULL DEFAULT 100.00"),
        ("aborted_connections", "INT NOT NULL DEFAULT 0"),
    ]
    with connection.cursor() as cursor:
        cursor.execute("DESCRIBE server_health;")
        existing = {row[0] for row in cursor.fetchall()}
        for col_name, col_def in columns_to_add:
            if col_name not in existing:
                cursor.execute(
                    f"ALTER TABLE server_health ADD COLUMN {col_name} {col_def};"
                )


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0026_serverhealth_max_connections'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='serverhealth',
                    name='blocked_queries',
                    field=models.IntegerField(default=0, help_text='Currently blocked/lock-waiting DB sessions'),
                ),
                migrations.AddField(
                    model_name='serverhealth',
                    name='long_running_queries',
                    field=models.IntegerField(default=0, help_text='Active DB queries exceeding latency threshold'),
                ),
                migrations.AddField(
                    model_name='serverhealth',
                    name='cache_hit_percent',
                    field=models.DecimalField(decimal_places=2, default=100.0, help_text='Engine buffer/cache hit ratio %', max_digits=5),
                ),
                migrations.AddField(
                    model_name='serverhealth',
                    name='aborted_connections',
                    field=models.IntegerField(default=0, help_text='Aborted/refused DB connection attempts delta'),
                ),
            ],
            database_operations=[
                migrations.RunPython(
                    add_db_perf_columns_if_not_exists,
                    reverse_code=migrations.RunPython.noop,
                ),
            ],
        ),
    ]
