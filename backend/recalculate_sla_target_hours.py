import os
import django
import sys
import calendar
from datetime import date

# Configure Django settings
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dba_tracker.settings')
django.setup()

from api.models import SLAConfig, UgandaHoliday

def recalculate_all_sla_hours():
    records = SLAConfig.objects.all()
    print(f"Found {records.count()} SLA Configuration records. Recalculating target hours...")
    
    updated_count = 0
    for rec in records:
        year = rec.month.year
        month = rec.month.month
        db = rec.database
        
        # Fetch all holidays for this month
        holidays = set(UgandaHoliday.objects.filter(
            holiday_date__year=year,
            holiday_date__month=month
        ).values_list('holiday_date', flat=True))
        
        _, num_days = calendar.monthrange(year, month)
        calculated_val = 0
        
        for day in range(1, num_days + 1):
            d = date(year, month, day)
            is_holiday = d in holidays
            is_weekend = d.weekday() >= 5  # 5 is Saturday, 6 is Sunday
            
            if not is_holiday:
                if db == 'Oracle':
                    calculated_val += 18
                else:
                    if not is_weekend:
                        calculated_val += 9
        
        old_hours = rec.target_hours
        rec.target_hours = calculated_val
        rec.save()
        print(f"- {db} ({rec.month.strftime('%B %Y')}): {old_hours} hrs -> {calculated_val} hrs")
        updated_count += 1
        
    print(f"\nSuccessfully updated {updated_count} records.")

if __name__ == "__main__":
    recalculate_all_sla_hours()
