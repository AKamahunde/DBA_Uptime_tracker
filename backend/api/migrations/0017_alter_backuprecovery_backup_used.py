from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('api', '0016_rename_targethours_slaconfig_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='backuprecovery',
            name='backup_used',
            field=models.CharField(max_length=255),
        ),
    ]
