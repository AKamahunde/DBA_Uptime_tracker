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
    is_service_desk = db_name in ('servicedesk', 'service desk', 'service_desk')
    is_explicit_db = (
        is_service_desk
        or db_name in ('mysql', 'ms sql server', 'mssql', 'sql server', 'postgresql', 'postgres', 'oracle')
        or ('sql' in db_name)
    )
    is_infra = (not is_explicit_db) and any(
        kw in db_name for kw in ['infra', 'infrastructure', 'host', 'node', 'server', 'olkvm', 'proxmox', '24/7', '24x7']
    )
    if is_infra:
        # Infrastructure host nodes operate 24/7 (24 hours/day, 7 days/week) with no holiday exclusions
        total_seconds = Decimal(str((end_local - start_local).total_seconds()))
        hours = total_seconds / Decimal('3600.0')
        return round(hours, 2)

    is_oracle = (not is_service_desk) and any(kw in db_name for kw in ['oracle', 'nmsinfra', 'nmsebs'])
    
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


def calculate_service_desk_resolution_hours(start_dt, end_dt, holiday_dates_override=None):
    """
    Calculates Service Desk incident resolution duration strictly against the
    08:00:00 to 17:00:00 (9 hours/day), Monday to Friday business clock,
    excluding Uganda statutory public holidays (IT Service Desk Policy §20.4.5.2).
    Applied uniformly to SLA-005 through SLA-012 across all database and
    infrastructure platforms.
    """
    if not start_dt or not end_dt:
        return Decimal('0.00')
    return calculate_operational_downtime(
        start_dt,
        end_dt,
        database='ServiceDesk',
        holiday_dates_override=holiday_dates_override,
    )


def merge_time_intervals(intervals):
    """
    Merges (unions) overlapping or adjacent (start_dt, end_dt) intervals into a
    sorted list of disjoint timezone-aware intervals so simultaneous host hypervisor
    and guest VM outages are never double-counted.
    """
    if not intervals:
        return []

    tz = timezone.get_current_timezone()
    normalized = []
    for item in intervals:
        if not item or len(item) != 2:
            continue
        start_dt, end_dt = item
        if not start_dt or not end_dt:
            continue
        start_local = timezone.make_aware(start_dt, tz) if timezone.is_naive(start_dt) else timezone.localtime(start_dt, tz)
        end_local = timezone.make_aware(end_dt, tz) if timezone.is_naive(end_dt) else timezone.localtime(end_dt, tz)
        if end_local > start_local:
            normalized.append((start_local, end_local))

    if not normalized:
        return []

    normalized.sort(key=lambda pair: (pair[0], pair[1]))
    merged = [normalized[0]]
    for cur_start, cur_end in normalized[1:]:
        prev_start, prev_end = merged[-1]
        if cur_start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, cur_end))
        else:
            merged.append((cur_start, cur_end))
    return merged


def calculate_merged_operational_downtime(
    timed_intervals,
    untimed_hours=0.0,
    database='Infrastructure',
    holiday_dates_override=None,
):
    """
    Sums operational downtime over the union of timed_intervals plus any legacy
    untimed duration_hrs values, returning total hours rounded to 2 decimal places.
    """
    merged = merge_time_intervals(timed_intervals)
    total_hours = float(untimed_hours or 0.0)
    for m_start, m_end in merged:
        total_hours += float(
            calculate_operational_downtime(
                m_start,
                m_end,
                database=database,
                holiday_dates_override=holiday_dates_override,
            )
        )
    return round(total_hours, 2)
