from django.db import migrations
from django.db.models import Q

def fix_corrupted_mysql_backups(apps, schema_editor):
    BackupRecovery = apps.get_model('api', 'BackupRecovery')
    
    # Locate all MySQL backup records where backup_used contains binlog, checkpoint, or NFS, or is uncompressed
    records = BackupRecovery.objects.filter(
        Q(database='MySQL') | Q(database_instance__icontains='nickle'),
        Q(backup_used__icontains='binlog') | Q(backup_used__icontains='checkpoint') | Q(backup_used__icontains='NFS') | Q(compression=False)
    )
    
    for r in records:
        start_str = r.start.strftime('%Y-%m-%d_%H-%M-%S') if r.start else f"{r.date}_22-00-00"
        b_type = (r.backup_type or 'full').lower()
        r.backup_used = f"nickledb_{start_str}_{b_type}.tar.gz"
        r.compression = True
        r.save()

class Migration(migrations.Migration):

    dependencies = [
        ('api', '0017_alter_backuprecovery_backup_used'),
    ]

    operations = [
        migrations.RunPython(fix_corrupted_mysql_backups, reverse_code=migrations.RunPython.noop),
    ]
