import os
import django
from django.utils import timezone

# Configure Django settings environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dba_tracker.settings')
django.setup()

from api.views import auto_close_incidents, auto_update_maintenance_statuses

def main():
    timestamp = timezone.localtime(timezone.now()).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] Running background cron tasks...")
    
    try:
        auto_close_incidents()
        print("- Resolved incidents auto-closed check completed.")
    except Exception as e:
        print(f"- Error during incident auto-close: {e}")
        
    try:
        auto_update_maintenance_statuses()
        print("- Maintenance windows status promotion check completed.")
    except Exception as e:
        print(f"- Error during maintenance status promotion: {e}")
        
    print("Background tasks run complete.")

if __name__ == '__main__':
    main()
