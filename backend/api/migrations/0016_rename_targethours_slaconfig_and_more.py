from django.db import migrations, models
from api.migrations.0014_apply_database_views import apply_sql_views


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
