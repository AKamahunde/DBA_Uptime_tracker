from django.db import migrations, models


def add_max_connections_if_not_exists(apps, schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute("DESCRIBE server_health;")
        cols = [row[0] for row in cursor.fetchall()]
        if 'max_connections' not in cols:
            cursor.execute("ALTER TABLE server_health ADD COLUMN max_connections INT NOT NULL DEFAULT 0;")


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0025_create_system_administration_group'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='serverhealth',
                    name='max_connections',
                    field=models.IntegerField(default=0, help_text='Configured max_connections limit'),
                ),
            ],
            database_operations=[
                migrations.RunPython(
                    add_max_connections_if_not_exists,
                    reverse_code=migrations.RunPython.noop,
                ),
            ],
        ),
    ]
