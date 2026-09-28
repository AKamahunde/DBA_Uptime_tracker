import os
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dba_tracker.settings')
django.setup()

from api.models import BackupRecovery
from django.db.models import Q

def main():
    print("Searching for MySQL backup records with invalid references or uncompressed flags...")
    records = BackupRecovery.objects.filter(
        Q(database='MySQL') | Q(database_instance__icontains='nickle'),
        Q(backup_used__icontains='binlog') | Q(backup_used__icontains='checkpoint') | Q(backup_used__icontains='NFS') | Q(compression=False)
    )
    
    if not records.exists():
        print("No affected records found. All MySQL backup records look healthy!")
        return
        
    print(f"Found {records.count()} record(s) to fix:\n")
    for r in records:
        print(f"Record ID: {r.id} | Date: {r.date} | Start: {r.start}")
        print(f"  Old Reference:   {repr(r.backup_used)}")
        print(f"  Old Compression: {r.compression}")
        
        # Calculate clean archive filename
        start_str = r.start.strftime('%Y-%m-%d_%H-%M-%S') if r.start else f"{r.date}_22-00-00"
        b_type = (r.backup_type or 'full').lower()
        new_filename = f"nickledb_{start_str}_{b_type}.tar.gz"
        
        r.backup_used = new_filename
        r.compression = True
        r.save()
        
        print(f"  New Reference:   {repr(r.backup_used)}")
        print(f"  New Compression: {r.compression}")
        print("  --> Updated successfully.\n")

    print("All affected MySQL backup records have been corrected.")

if __name__ == '__main__':
    main()
