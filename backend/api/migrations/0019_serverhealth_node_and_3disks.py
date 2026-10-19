from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0018_fix_mysql_backup_records'),
    ]

    operations = [
        migrations.AddField(
            model_name='serverhealth',
            name='node_name',
            field=models.CharField(
                blank=True,
                choices=[('OLKVM', 'OLKVM Node'), ('Proxmox', 'Proxmox Node')],
                default='Proxmox',
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='hosted_systems',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='uptime_percent',
            field=models.DecimalField(
                decimal_places=2,
                default=100.0,
                max_digits=5,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100),
                ],
            ),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='uptime_days',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=8),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='logical_cpus',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='memory_total_gb',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=10),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='memory_used_gb',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=10),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='storage_growth_gb',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=10),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='storage_growth_pct',
            field=models.DecimalField(decimal_places=3, default=0.0, max_digits=7),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='disk_1_type',
            field=models.CharField(blank=True, default='Disk 1 (SSD / OS)', max_length=100),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='disk_1_size_gb',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=12),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='disk_1_used_gb',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=12),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='disk_1_percent',
            field=models.DecimalField(
                decimal_places=2,
                default=0.0,
                max_digits=5,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100),
                ],
            ),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='disk_2_type',
            field=models.CharField(blank=True, default='Disk 2 (SAS / Data)', max_length=100),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='disk_2_size_gb',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=12),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='disk_2_used_gb',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=12),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='disk_2_percent',
            field=models.DecimalField(
                decimal_places=2,
                default=0.0,
                max_digits=5,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100),
                ],
            ),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='disk_3_type',
            field=models.CharField(blank=True, default='Disk 3 (SAN / Backup)', max_length=100),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='disk_3_size_gb',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=12),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='disk_3_used_gb',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=12),
        ),
        migrations.AddField(
            model_name='serverhealth',
            name='disk_3_percent',
            field=models.DecimalField(
                decimal_places=2,
                default=0.0,
                max_digits=5,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100),
                ],
            ),
        ),
        migrations.AlterField(
            model_name='serverhealth',
            name='entered_by',
            field=models.CharField(
                choices=[('User', 'User'), ('Script', 'Script'), ('Prometheus', 'Prometheus')],
                default='User',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='serverhealth',
            name='connections',
            field=models.IntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='serverhealth',
            name='slow_queries',
            field=models.IntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='serverhealth',
            name='threads',
            field=models.IntegerField(default=0),
        ),
    ]
