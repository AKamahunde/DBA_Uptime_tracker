from datetime import datetime, time, timedelta
from decimal import Decimal
from django.utils import timezone

def calculate_operational_downtime(start_dt, end_dt, database='MySQL', holiday_dates_override=None):
    """
    Intelligently calculates the exact operational downtime hours between start_dt and end_dt
    that intersect with the database's business SLA operating window:
    
    1. Oracle:
       - Operating window: 06:00:00 to 24:00:00 (18 hours/day)
       - Schedule: 7 days/week (Monday to Sunday)
       - Excludes: Uganda public holidays
       
    2. Non-Oracle (MySQL, MS SQL Server, PostgreSQL, etc.):
       - Operating window: 08:00:00 to 17:00:00 (9 hours/day)
       - Schedule: 5 days/week (Monday to Friday)
       - Excludes: Weekends (Saturday, Sunday) and Uganda public holidays
    """
    if not start_dt or not end_dt:
        return Decimal('0.00')
        
    tz = timezone.get_current_timezone()
    
    # Ensure datetimes are timezone-aware in the application's local timezone
    if timezone.is_naive(start_dt):
        start_local = timezone.make_aware(start_dt, tz)
    else:
        start_local = timezone.localtime(start_dt, tz)
        
    if timezone.is_naive(end_dt):
        end_local = timezone.make_aware(end_dt, tz)
    else:
        end_local = timezone.localtime(end_dt, tz)
        
    if start_local >= end_local:
        return Decimal('0.00')
        
    start_date = start_local.date()
    end_date = end_local.date()
    
    if holiday_dates_override is not None:
        holiday_dates = set(holiday_dates_override)
    else:
        holiday_dates = set()
        try:
            from api.models import UgandaHoliday
            holiday_dates = set(UgandaHoliday.objects.filter(
                holiday_date__range=(start_date, end_date)
            ).values_list('holiday_date', flat=True))
        except Exception:
            # Fallback if DB table is unavailable or offline
            holiday_dates = set()
    
    db_name = (database or '').strip().lower()
    is_oracle = any(kw in db_name for kw in ['oracle', 'nmsinfra', 'nmsebs'])
    
    total_seconds = Decimal('0.0')
    cur_date = start_date
    
    while cur_date <= end_date:
        # Check if the day is a registered Uganda holiday
        if cur_date not in holiday_dates:
            if is_oracle:
                # Oracle: 06:00 to 24:00 (midnight), 7 days a week
                win_start = timezone.make_aware(datetime.combine(cur_date, time(6, 0, 0)), tz)
                win_end = timezone.make_aware(datetime.combine(cur_date + timedelta(days=1), time(0, 0, 0)), tz)
                
                overlap_start = max(start_local, win_start)
                overlap_end = min(end_local, win_end)
                if overlap_end > overlap_start:
                    total_seconds += Decimal(str((overlap_end - overlap_start).total_seconds()))
            else:
                # Non-Oracle (MySQL, MSSQL, etc.): 08:00 to 17:00, Monday to Friday
                if cur_date.weekday() < 5:  # 0-4 are Mon-Fri, 5 is Sat, 6 is Sun
                    win_start = timezone.make_aware(datetime.combine(cur_date, time(8, 0, 0)), tz)
                    win_end = timezone.make_aware(datetime.combine(cur_date, time(17, 0, 0)), tz)
                    
                    overlap_start = max(start_local, win_start)
                    overlap_end = min(end_local, win_end)
                    if overlap_end > overlap_start:
                        total_seconds += Decimal(str((overlap_end - overlap_start).total_seconds()))
                        
        cur_date += timedelta(days=1)
        
    hours = total_seconds / Decimal('3600.0')
    return round(hours, 2)
