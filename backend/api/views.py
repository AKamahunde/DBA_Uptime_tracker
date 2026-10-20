from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, HttpResponse
import json
import os
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal
from .models import (
    IncidentRegister, ReplicationMonitor, BackupRecovery, ServerHealth,
    MaintenanceRegister, CapacityPlanning, SLAConfig, IncidentChangeLog, UgandaHoliday,
    IncidentStaging
)
from .forms import (
    IncidentRegisterForm, ReplicationMonitorForm, BackupRecoveryForm,
    ServerHealthForm, MaintenanceRegisterForm, CapacityPlanningForm,
    SLAConfigForm, UgandaHolidayForm
)
from users.models import User
from django.db.models import Sum, Count, Avg
from .sla_utils import calculate_operational_downtime

def about(request):
    return render(request, 'about.html')


def auto_close_incidents():
    # Auto-closes incidents resolved for more than 24 hours (1 day)
    cutoff = timezone.now() - timedelta(days=1)
    resolved_incidents = IncidentRegister.objects.filter(
        status='Resolved',
        end__lte=cutoff
    )
    for incident in resolved_incidents:
        incident.status = 'Closed'
        incident.save()
        IncidentChangeLog.objects.create(
            incident=incident,
            changed_by=None,  # System
            action="Auto-closed by system after 24 hours of resolution",
            new_status="Closed"
        )

def auto_update_maintenance_statuses():
    # Auto-updates maintenance windows based on start and end times
    now = timezone.now()
    
    # 1. Scheduled -> In Progress: if start <= now < end
    active_maintenances = MaintenanceRegister.objects.filter(
        status='Scheduled',
        start__lte=now,
        end__gt=now
    )
    for maint in active_maintenances:
        maint.status = 'In Progress'
        maint.save()
        
    # 2. Scheduled/In Progress -> Completed: if end <= now
    passed_maintenances = MaintenanceRegister.objects.filter(
        end__lte=now,
        status__in=['Scheduled', 'In Progress']
    )
    for maint in passed_maintenances:
        maint.status = 'Completed'
        maint.save()

def autopopulate_fixed_holidays():
    from datetime import date
    current_year = date.today().year
    years_to_check = [current_year, current_year + 1]
    
    fixed_holidays = [
        ((1, 1), 'New Year'),
        ((1, 26), 'NRM Liberation'),
        ((2, 16), 'Luwum Day'),
        ((3, 8), 'Women Day'),
        ((5, 1), 'Labour Day'),
        ((6, 3), 'Martyr Day'),
        ((6, 9), 'Heroes Day'),
        ((10, 9), 'Independence Day'),
        ((12, 25), 'Christmas'),
        ((12, 26), 'Boxing Day')
    ]
    
    for y in years_to_check:
        for (month, day), name in fixed_holidays:
            try:
                h_date = date(y, month, day)
                UgandaHoliday.objects.get_or_create(
                    holiday_date=h_date,
                    defaults={'name': name}
                )
            except Exception as e:
                pass

@login_required
def dashboard(request):
    autopopulate_fixed_holidays()
    auto_close_incidents()
    
    # Run all counts in a single DB round-trip by using lazy evaluation
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                (SELECT COUNT(*) FROM incident_register),
                (SELECT COUNT(*) FROM replication_monitor),
                (SELECT COUNT(*) FROM backup_recovery),
                (SELECT COUNT(*) FROM server_health),
                (SELECT COUNT(*) FROM maintenance_register),
                (SELECT COUNT(*) FROM capacity_planning)
        """)
        row = cursor.fetchone()

    # Get reporting month
    month_str = request.GET.get('month', '')
    if month_str:
        try:
            reporting_month = datetime.strptime(month_str, '%Y-%m').date()
        except ValueError:
            reporting_month = timezone.now().date().replace(day=1)
    else:
        reporting_month = timezone.now().date().replace(day=1)

def get_status(current, target_val, operator, warning_val=None):
    if current == 'No Data' or current is None or current == 'N/A':
        return 'N/A'
    try:
        if isinstance(current, str) and '%' in current:
            current = float(current.replace('%', ''))
        else:
            current = float(current)
        
        if operator == '>=':
            if current >= target_val:
                return 'Meets SLA'
            elif warning_val is not None and current >= warning_val:
                return 'Warning'
            else:
                return 'Below SLA'
        elif operator == '<=':
            if current > target_val:
                return 'Below SLA'
            elif warning_val is not None and current >= warning_val:
                return 'Warning'
            else:
                return 'Meets SLA'
    except:
        return 'N/A'
    return 'N/A'

def _get_dashboard_data(reporting_month):
    db_types = ['Oracle', 'MySQL', 'MS SQL Server']

    dashboard_data = []

    for db_type in db_types:
        # Planned Downtime
        planned = MaintenanceRegister.objects.filter(
            database__iexact=db_type, success=True, date__year=reporting_month.year, date__month=reporting_month.month
        ).aggregate(Sum('duration_hrs'))['duration_hrs__sum'] or 0

        # Unplanned Downtime & MTTR & Incidents
        # ALL incidents for the month
        incidents = IncidentRegister.objects.filter(
            database__iexact=db_type, date__year=reporting_month.year, date__month=reporting_month.month
        )
        total_incidents = incidents.count()

        # Only UNPLANNED incidents affect availability, MTTR and the incident volume KPI
        unplanned_incidents = incidents.filter(planned=False)
        total_unplanned = unplanned_incidents.count()

        # IT Service Desk Policy §20.4.5.2 — resolution targets per severity (in hours)
        SLA_RESOLUTION_TARGETS = {
            'Critical': 9,    # 1 working day
            'High': 9,        # 1 working day
            'Medium': 18,     # 2 working days
            'Low': 180,       # 4 weeks
        }

        unplanned = 0
        sla_breach_count = 0
        now = timezone.now()
        for inc in unplanned_incidents:
            if not inc.end and inc.status not in ['Resolved', 'Closed'] and inc.start:
                active_downtime = calculate_operational_downtime(inc.start, now, inc.database)
                unplanned += float(active_downtime)
            elif inc.duration_hrs:
                unplanned += float(inc.duration_hrs)
                target_hrs = SLA_RESOLUTION_TARGETS.get(inc.severity, 9)
                if float(inc.duration_hrs) > target_hrs:
                    sla_breach_count += 1

        mttr_hrs = (unplanned / total_unplanned) if total_unplanned > 0 else 0
        mttr_mins = round(mttr_hrs * 60, 2)

        # Target Hours (Availability)
        sla_config_record = SLAConfig.objects.filter(
            database__iexact=db_type, month__year=reporting_month.year, month__month=reporting_month.month
        ).first()
        
        sla_exists = (sla_config_record is not None)
        th = sla_config_record.target_hours if sla_exists else 0

        # Auto-ensure default target hours and SLA target if not manually configured
        if not sla_exists or not th:
            import calendar
            from datetime import date
            yr = reporting_month.year
            mo = reporting_month.month
            holidays = set(UgandaHoliday.objects.filter(
                holiday_date__year=yr, holiday_date__month=mo
            ).values_list('holiday_date', flat=True))
            _, num_days = calendar.monthrange(yr, mo)
            calc_th = 0
            for day in range(1, num_days + 1):
                d = date(yr, mo, day)
                if d not in holidays:
                    if db_type == 'Oracle':
                        calc_th += 18
                    elif d.weekday() < 5:
                        calc_th += 9
            if calc_th > 0:
                try:
                    target_pct_default = 99.50 if db_type == 'MySQL' else 99.95
                    first_of_month = reporting_month.replace(day=1)
                    sla_config_record, _ = SLAConfig.objects.get_or_create(
                        database=db_type,
                        month=first_of_month,
                        defaults={'target_hours': calc_th, 'sla_percent': target_pct_default}
                    )
                    sla_exists = True
                    th = sla_config_record.target_hours
                except Exception:
                    th = calc_th
                    sla_exists = True

        if th and th > 0:
            avail = ((float(th) - float(unplanned)) / float(th)) * 100
            avail_str = f"{round(avail, 2)}%"
        else:
            avail_str = 'No Data'

        # Backups
        backups = BackupRecovery.objects.filter(
            database__iexact=db_type, record_type='Backup', date__year=reporting_month.year, date__month=reporting_month.month
        )
        total_backups = backups.count()
        success_backups = backups.filter(outcome='Success').count()
        failed_backups = backups.filter(outcome='Failed').count()
        backup_success_pct = f"{round((success_backups/total_backups)*100, 1)}%" if total_backups > 0 else 'No Data'

        # Restores
        restores = BackupRecovery.objects.filter(
            database__iexact=db_type, record_type='Recovery', date__year=reporting_month.year, date__month=reporting_month.month
        )
        total_restores = restores.count()
        success_restores = restores.filter(outcome='Success').count()
        restore_success_pct = f"{round((success_restores/total_restores)*100, 1)}%" if total_restores > 0 else 'No Data'

        # Replication Status
        latest_rep = ReplicationMonitor.objects.filter(
            database__iexact=db_type, date__year=reporting_month.year, date__month=reporting_month.month
        ).order_by('-date', '-created_at').first()
        rep_status = latest_rep.status if latest_rep else 'No Data'
        
        if db_type in ['MS SQL Server']:
            rep_status = 'N/A'

        # Health status calculation
        active_incidents = IncidentRegister.objects.filter(
            database__iexact=db_type,
            status__in=['Open', 'In Progress']
        ).count()
        
        failed_backups_count = BackupRecovery.objects.filter(
            database__iexact=db_type,
            record_type='Backup',
            outcome='Failed',
            date__year=reporting_month.year,
            date__month=reporting_month.month
        ).count()
        
        replication_issues = False
        if db_type in ['MySQL', 'Oracle']:
            latest_rep_check = ReplicationMonitor.objects.filter(
                database__iexact=db_type
            ).order_by('-date', '-created_at').first()
            if latest_rep_check and (latest_rep_check.status != 'Healthy' or latest_rep_check.severity == 'Critical'):
                replication_issues = True
                
        high_disk_usage = False
        capacity_record = CapacityPlanning.objects.filter(
            database__iexact=db_type,
            month__year=reporting_month.year,
            month__month=reporting_month.month
        ).order_by('-updated_at').first()
        
        if not capacity_record:
            capacity_record = CapacityPlanning.objects.filter(
                database__iexact=db_type
            ).order_by('-month', '-updated_at').first()
            
        if capacity_record and capacity_record.disk_used_percent and capacity_record.disk_used_percent > 85:
            high_disk_usage = True
            
        if active_incidents > 0:
            health_status = 'OUTAGE'
        elif failed_backups_count > 0 or replication_issues or high_disk_usage:
            health_status = 'WARNING'
        else:
            health_status = 'HEALTHY'

        # Last backup details
        last_backup = BackupRecovery.objects.filter(
            database__iexact=db_type,
            record_type='Backup'
        ).order_by('-date', '-created_at').first()
        
        last_backup_str = 'No Backups Logged'
        if last_backup:
            last_backup_str = f"{last_backup.outcome} - {last_backup.date.strftime('%d %b')}"

        target_pct = float(sla_config_record.sla_percent) if (sla_exists and sla_config_record.sla_percent) else (99.50 if db_type == 'MySQL' else 99.95)

        dashboard_data.append({
            'database': db_type,
            'availability': avail_str,
            'target_sla_val': target_pct,
            'availability_status': get_status(avail_str, target_pct, '>=') if sla_exists else 'N/A',
            'planned_downtime': round(planned, 2) if sla_exists else 'No Data',
            'planned_downtime_status': get_status(planned, 48.0, '<=') if sla_exists else 'N/A',
            'unplanned_downtime': round(unplanned, 2) if sla_exists else 'No Data',
            'unplanned_downtime_status': get_status(unplanned, 2.0, '<=') if sla_exists else 'N/A',
            'total_incidents': total_incidents if sla_exists else 'No Data',
            'total_incidents_status': get_status(total_unplanned, 4, '<=') if sla_exists else 'N/A',
            'sla_breach_count': sla_breach_count if sla_exists else 0,
            'mttr_mins': mttr_mins if sla_exists else 'No Data',
            'mttr_mins_status': get_status(mttr_mins, 240, '<=') if sla_exists else 'N/A',
            'backup_success': backup_success_pct,
            'backup_success_status': get_status(backup_success_pct, 95.0, '>=') if (sla_exists and backup_success_pct != 'No Data') else 'N/A',
            'restore_success': restore_success_pct,
            'restore_success_status': get_status(restore_success_pct, 80.0, '>=') if (sla_exists and restore_success_pct != 'No Data') else 'N/A',
            'replication_status': rep_status,
            'replication_status_status': 'N/A' if (rep_status in ['No Data', 'N/A'] or not sla_exists) else ('Meets SLA' if rep_status == 'Healthy' else 'Below SLA'),
            'failed_backups': failed_backups if sla_exists else 'No Data',
            'health_status': health_status,
            'active_incidents': active_incidents,
            'last_backup_detail': last_backup_str,
            
            # Disk space and tablespace capacity planning values
            'database_size_gb': float(capacity_record.database_size_gb) if (capacity_record and capacity_record.database_size_gb is not None) else 'No Data',
            'available_capacity_gb': float(capacity_record.available_capacity_gb) if (capacity_record and capacity_record.available_capacity_gb is not None) else 'No Data',
            'disk_used_percent': float(capacity_record.disk_used_percent) if (capacity_record and capacity_record.disk_used_percent is not None) else 'No Data',
            'tablespace_used_percent': float(capacity_record.tablespace_used_percent) if (capacity_record and capacity_record.tablespace_used_percent is not None) else 'No Data',
            'growth_gb': float(capacity_record.growth_gb) if (capacity_record and capacity_record.growth_gb is not None) else 'No Data',
        })
    return dashboard_data, db_types

def _get_database_detail(selected_db, reporting_month, dashboard_data):
    if selected_db in ['MKInsight', 'Pentana']:
        selected_db = 'MS SQL Server'
    database_detail = None
    db_types = ['Oracle', 'MySQL', 'MS SQL Server']
    if selected_db in db_types:
        row_data = next((item for item in dashboard_data if item['database'] == selected_db), None)
        if row_data:
            # Query if SLA Config exists for the selected database
            selected_sla_exists = SLAConfig.objects.filter(
                database__iexact=selected_db, month__year=reporting_month.year, month__month=reporting_month.month
            ).exists()
            # Query additional details for SLA table
            # Last Full Backup
            last_full = BackupRecovery.objects.filter(
                database__iexact=selected_db, record_type='Backup', backup_type='Full', outcome='Success'
            ).order_by('-date').first()
            last_full_str = last_full.date.strftime('%d/%m/%Y') if last_full else 'No Data'
            
            # Last Incremental Backup
            last_inc = BackupRecovery.objects.filter(
                database__iexact=selected_db, record_type='Backup', backup_type='Incremental', outcome='Success'
            ).order_by('-date').first()
            last_inc_str = last_inc.date.strftime('%d/%m/%Y') if last_inc else 'No Data'
            
            # Last DR Test
            last_dr = BackupRecovery.objects.filter(
                database__iexact=selected_db, record_type='Recovery', outcome='Success'
            ).order_by('-date').first()
            last_dr_str = last_dr.date.strftime('%d/%m/%Y') if last_dr else 'No Data'
            
            # Failed Restore Tests
            failed_restores = BackupRecovery.objects.filter(
                database__iexact=selected_db, record_type='Recovery', date__year=reporting_month.year, date__month=reporting_month.month, outcome='Failed'
            ).count()
            
            # Replication Lag
            latest_rep_detail = ReplicationMonitor.objects.filter(
                database__iexact=selected_db, date__year=reporting_month.year, date__month=reporting_month.month
            ).order_by('-date', '-created_at').first()
            
            rep_lag = latest_rep_detail.seconds_behind if latest_rep_detail and latest_rep_detail.seconds_behind is not None else 'No Data'
            if rep_lag != 'No Data':
                rep_lag_mins = round(rep_lag / 60, 2)
                rep_lag_str = f"{rep_lag_mins} mins" if rep_lag_mins > 0 else "0 mins"
            else:
                rep_lag_str = "No Data"
                
            if selected_db in ['MS SQL Server']:
                rep_lag_str = 'N/A'

            # Calculate Statuses
            avail_status = get_status(row_data['availability'], 99.95, '>=', 99.90) if selected_sla_exists else 'N/A'
            planned_status = get_status(row_data['planned_downtime'], 48.0, '<=', 40.0) if selected_sla_exists else 'N/A'
            unplanned_status = get_status(row_data['unplanned_downtime'], 2.0, '<=', 1.5) if selected_sla_exists else 'N/A'
            incidents_status = get_status(row_data['total_incidents'], 4, '<=', 3) if selected_sla_exists else 'N/A'
            mttr_status = get_status(row_data['mttr_mins'], 240, '<=', 180) if selected_sla_exists else 'N/A'
            backup_status = get_status(row_data['backup_success'], 95.0, '>=', 90.0) if (selected_sla_exists and row_data['backup_success'] != 'No Data') else 'N/A'
            restore_status = get_status(row_data['restore_success'], 80.0, '>=', 75.0) if (selected_sla_exists and row_data['restore_success'] != 'No Data') else 'N/A'
            
            if row_data['replication_status'] in ['No Data', 'N/A'] or not selected_sla_exists:
                rep_status_check = 'N/A'
            else:
                rep_status_check = 'Meets SLA' if row_data['replication_status'] == 'Healthy' else 'Below SLA'
            
            # Date checks
            now = timezone.now().date()
            if last_full:
                days_since_full = (now - last_full.date).days
                full_status = 'Meets SLA' if days_since_full < 5 else ('Warning' if days_since_full < 7 else 'Below SLA')
            else:
                full_status = 'N/A'
                
            if last_inc:
                days_since_inc = (now - last_inc.date).days
                inc_status = 'Meets SLA' if days_since_inc <= 1 else ('Warning' if days_since_inc <= 2 else 'Below SLA') # 24 hrs
            else:
                inc_status = 'N/A'
                
            if last_dr:
                days_since_dr = (now - last_dr.date).days
                dr_status = 'Meets SLA' if days_since_dr <= 300 else ('Warning' if days_since_dr <= 365 else 'Below SLA')
            else:
                dr_status = 'N/A'

            failed_backup_status = 'Meets SLA' if (row_data['failed_backups'] == 0 and selected_sla_exists) else ('Warning' if (row_data['failed_backups'] == 1 and selected_sla_exists) else ('Below SLA' if selected_sla_exists else 'N/A'))
            failed_restore_status = 'Meets SLA' if (failed_restores == 0 and selected_sla_exists) else ('Warning' if (failed_restores == 1 and selected_sla_exists) else ('Below SLA' if selected_sla_exists else 'N/A'))
            
            rep_lag_status = 'Below SLA'
            if rep_lag not in ['No Data', 'N/A'] and selected_sla_exists:
                lag_mins = rep_lag / 60
                if lag_mins < 2:
                    rep_lag_status = 'Meets SLA'
                elif lag_mins <= 5:
                    rep_lag_status = 'Warning'
                else:
                    rep_lag_status = 'Below SLA'
            else:
                rep_lag_status = 'N/A'

            database_detail = {
                'database': selected_db,
                'current': {
                    'availability': row_data['availability'],
                    'planned_downtime': row_data['planned_downtime'],
                    'unplanned_downtime': row_data['unplanned_downtime'],
                    'total_incidents': row_data['total_incidents'],
                    'sla_breach_count': row_data.get('sla_breach_count', 0),
                    'mttr_mins': row_data['mttr_mins'],
                    'backup_success': row_data['backup_success'],
                    'restore_success': row_data['restore_success'],
                    'replication_status': row_data['replication_status'],
                    'last_full': last_full_str,
                    'last_inc': last_inc_str,
                    'last_dr': last_dr_str,
                    'failed_backups': row_data['failed_backups'],
                    'failed_restores': failed_restores,
                    'replication_lag': rep_lag_str
                },
                'status': {
                    'availability': avail_status,
                    'planned_downtime': planned_status,
                    'unplanned_downtime': unplanned_status,
                    'total_incidents': incidents_status,
                    'mttr_mins': mttr_status,
                    'backup_success': backup_status,
                    'restore_success': restore_status,
                    'replication_status': rep_status_check,
                    'last_full': full_status,
                    'last_inc': inc_status,
                    'last_dr': dr_status,
                    'failed_backups': failed_backup_status,
                    'failed_restores': failed_restore_status,
                    'replication_lag': rep_lag_status
                }
            }
    return database_detail

@login_required
def dashboard(request):
    autopopulate_fixed_holidays()
    auto_close_incidents()
    auto_update_maintenance_statuses()
    
    # Run all counts in a single DB round-trip by using lazy evaluation
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                (SELECT COUNT(*) FROM incident_register),
                (SELECT COUNT(*) FROM replication_monitor),
                (SELECT COUNT(*) FROM backup_recovery),
                (SELECT COUNT(*) FROM server_health),
                (SELECT COUNT(*) FROM maintenance_register),
                (SELECT COUNT(*) FROM capacity_planning)
        """)
        row = cursor.fetchone()

    # Get reporting month
    month_str = request.GET.get('month', '')
    if month_str:
        try:
            reporting_month = datetime.strptime(month_str, '%Y-%m').date()
        except ValueError:
            reporting_month = timezone.now().date().replace(day=1)
    else:
        reporting_month = timezone.now().date().replace(day=1)

    selected_db = request.GET.get('database_select', 'All Databases')
    if selected_db in ['MKInsight', 'Pentana']:
        selected_db = 'MS SQL Server'
    dashboard_data, db_types = _get_dashboard_data(reporting_month)
    database_detail = _get_database_detail(selected_db, reporting_month, dashboard_data)

    # KPI Calculations for Dashboard Redesign
    import math
    
    # Filter dashboard_data by selected_db if a specific database is selected
    filtered_dashboard_data = dashboard_data
    if selected_db != 'All Databases':
        filtered_dashboard_data = [item for item in dashboard_data if item['database'].lower() == selected_db.lower()]
        
    # 1. Overall Availability & Change
    avail_list = []
    for item in filtered_dashboard_data:
        val = item['availability']
        if val != 'No Data':
            try:
                avail_list.append(float(str(val).replace('%', '')))
            except ValueError:
                pass
    kpi_availability = f"{round(sum(avail_list)/len(avail_list), 2)}%" if avail_list else "No Data"
    
    # Previous month delta
    if reporting_month.month == 1:
        prev_month = reporting_month.replace(year=reporting_month.year - 1, month=12)
    else:
        prev_month = reporting_month.replace(month=reporting_month.month - 1)
        
    prev_dashboard_data, _ = _get_dashboard_data(prev_month)
    filtered_prev_dashboard_data = prev_dashboard_data
    if selected_db != 'All Databases':
        filtered_prev_dashboard_data = [item for item in prev_dashboard_data if item['database'].lower() == selected_db.lower()]
        
    prev_avail_list = []
    for item in filtered_prev_dashboard_data:
        val = item['availability']
        if val != 'No Data':
            try:
                prev_avail_list.append(float(str(val).replace('%', '')))
            except ValueError:
                pass
                
    kpi_availability_change = ""
    if avail_list and prev_avail_list:
        current_val = sum(avail_list)/len(avail_list)
        prev_val = sum(prev_avail_list)/len(prev_avail_list)
        diff = current_val - prev_val
        if diff > 0:
            kpi_availability_change = f"▲ +{round(diff, 2)}%"
        elif diff < 0:
            kpi_availability_change = f"▼ {round(diff, 2)}%"
        else:
            kpi_availability_change = "0.00%"
    else:
        kpi_availability_change = "N/A"
            
    # 2. SLA Compliance (databases meeting SLA)
    met_count = 0
    total_count = 0
    for item in filtered_dashboard_data:
        sla_configured = SLAConfig.objects.filter(
            database__iexact=item['database'], month__year=reporting_month.year, month__month=reporting_month.month
        ).exists()
        if sla_configured:
            total_count += 1
            if item['availability_status'] == 'Meets SLA':
                met_count += 1
    kpi_sla_compliance = f"{met_count}/{total_count} Met" if total_count > 0 else "N/A"
    
    # 3. Incidents
    incidents_qs = IncidentRegister.objects.filter(
        date__year=reporting_month.year, date__month=reporting_month.month
    )
    open_incidents_qs = IncidentRegister.objects.filter(
        status__in=['Open', 'In Progress']
    )
    if selected_db != 'All Databases':
        incidents_qs = incidents_qs.filter(database__iexact=selected_db)
        open_incidents_qs = open_incidents_qs.filter(database__iexact=selected_db)
        
    kpi_incidents_total = incidents_qs.count()
    kpi_incidents_open = open_incidents_qs.count()
    
    # 4. Backups
    month_backups = BackupRecovery.objects.filter(
        record_type='Backup', date__year=reporting_month.year, date__month=reporting_month.month
    )
    if selected_db != 'All Databases':
        month_backups = month_backups.filter(database__iexact=selected_db)
        
    total_bk = month_backups.count()
    success_bk = month_backups.filter(outcome='Success').count()
    kpi_backup_success = f"{round((success_bk / total_bk) * 100, 1)}%" if total_bk > 0 else "No Data"
    kpi_backup_failed = month_backups.filter(outcome='Failed').count()
    
    # 5. Replication
    rep_streams = ['MySQL', 'Oracle']
    if selected_db != 'All Databases':
        rep_streams = [db for db in rep_streams if db.lower() == selected_db.lower()]
        
    healthy_count = 0
    total_rep_streams = 0
    for db in rep_streams:
        latest_rep = ReplicationMonitor.objects.filter(
            database__iexact=db, date__year=reporting_month.year, date__month=reporting_month.month
        ).order_by('-date', '-created_at').first()
        if latest_rep:
            total_rep_streams += 1
            if latest_rep.severity != 'Critical' and latest_rep.io_thread == 'Yes' and latest_rep.sql_thread == 'Yes':
                healthy_count += 1
    kpi_replication_status = f"{healthy_count}/{total_rep_streams} Healthy" if total_rep_streams > 0 else "N/A"

    # 6. Availability Trend (Last 6 Months)
    trend_months = []
    for i in range(5, -1, -1):
        m_year = reporting_month.year
        m_month = reporting_month.month - i
        while m_month <= 0:
            m_month += 12
            m_year -= 1
        t_month = reporting_month.replace(year=m_year, month=m_month)
        
        t_data, _ = _get_dashboard_data(t_month)
        filtered_t_data = t_data
        if selected_db != 'All Databases':
            filtered_t_data = [item for item in t_data if item['database'].lower() == selected_db.lower()]
            
        t_avails = []
        for item in filtered_t_data:
            val = item['availability']
            if val != 'No Data':
                try:
                    t_avails.append(float(str(val).replace('%', '')))
                except ValueError:
                    pass
        # All months before July 2026 should be 0 because we don't have data for them
        if t_month.year < 2026 or (t_month.year == 2026 and t_month.month < 7):
            avg_avail = 0.0
        else:
            avg_avail = round(sum(t_avails)/len(t_avails), 2) if t_avails else 100.0

        trend_months.append({
            'name': t_month.strftime('%b'),
            'value': avg_avail,
        })
        
    # Calculate dynamic target SLA based on database configurations
    target_sla_val = 99.95
    if selected_db != 'All Databases':
        sla_conf = SLAConfig.objects.filter(
            database__iexact=selected_db, month__year=reporting_month.year, month__month=reporting_month.month
        ).first()
        if sla_conf:
            target_sla_val = float(sla_conf.sla_percent)
        elif selected_db.lower() == 'mysql':
            target_sla_val = 99.50

    valid_trend_vals = [m['value'] for m in trend_months if m['value'] > 0.0]
    trend_recent_avg = round(sum(valid_trend_vals) / len(valid_trend_vals), 2) if valid_trend_vals else 100.0

    min_val = min([m['value'] for m in trend_months if m['value'] > 0.0] + [target_sla_val])
    if min_val <= 0.0:
        y_min = 0.0
    else:
        y_min = math.floor(min_val - 0.2) if min_val < 98.0 else 98.0
    y_max = 100.0
    y_range = max(0.1, y_max - y_min)
    
    points = []
    for idx, m in enumerate(trend_months):
        x = 42 + idx * 63
        m['x'] = x
        if m['value'] > 0.0:
            y = 22 + (100.0 - m['value']) * 96 / y_range
            points.append(f"{x},{y}")
            m['y'] = y
            m['text_y'] = y - 7
            m['has_data'] = True
        else:
            # Plot at baseline y=118 (bottom of visual chart area)
            y = 118
            points.append(f"{x},{y}")
            m['y'] = y
            m['text_y'] = 110
            m['has_data'] = False
    trend_polyline_points = " ".join(points)
    # Generate polygon points for the area chart fill
    trend_area_points = ""
    if points:
        first_x = trend_months[0]['x']
        last_x = trend_months[-1]['x']
        trend_area_points = f"{first_x},118 " + trend_polyline_points + f" {last_x},118"
    sla_y_coord = 22 + (100.0 - target_sla_val) * 96 / y_range
    
    # 7. Attention Required Alerts
    attention_items = []
    # Staging review alert
    staging_reviews_qs = IncidentStaging.objects.filter(status='Awaiting Review')
    if selected_db != 'All Databases':
        staging_reviews_qs = staging_reviews_qs.filter(database__iexact=selected_db)
    staging_reviews = staging_reviews_qs.count()
    if staging_reviews > 0:
        attention_items.append({
            'type': 'warning',
            'message': f"Staging Area: {staging_reviews} alert(s) awaiting review."
        })
    # Failed backups
    recent_failed_backups_qs = BackupRecovery.objects.filter(
        record_type='Backup', outcome='Failed', date__year=reporting_month.year, date__month=reporting_month.month
    )
    if selected_db != 'All Databases':
        recent_failed_backups_qs = recent_failed_backups_qs.filter(database__iexact=selected_db)
    recent_failed_backups = recent_failed_backups_qs.count()
    if recent_failed_backups > 0:
        attention_items.append({
            'type': 'danger',
            'message': f"Backups: {recent_failed_backups} backup failure(s) detected this month."
        })
    # Open incidents
    active_incidents = IncidentRegister.objects.filter(status__in=['Open', 'In Progress']).order_by('-created_at')
    if selected_db != 'All Databases':
        active_incidents = active_incidents.filter(database__iexact=selected_db)
    for inc in active_incidents:
        attention_items.append({
            'type': 'danger',
            'message': f"Open Incident: {inc.ticket or 'INC-'+str(inc.id)} ({inc.database}) &mdash; {inc.severity} priority."
        })
        
    # 8. Combined Recent Activity
    activities = []
    # Backups
    recent_backups = BackupRecovery.objects.all().order_by('-date', '-created_at')
    if selected_db != 'All Databases':
        recent_backups = recent_backups.filter(database__iexact=selected_db)
    recent_backups = recent_backups[:8]
    for r in recent_backups:
        sort_time = r.created_at if r.created_at else timezone.now().replace(year=r.date.year, month=r.date.month, day=r.date.day, hour=0, minute=0, second=0, microsecond=0)
        activities.append({
            'sort_time': sort_time,
            'time_str': r.created_at.strftime('%H:%M') if r.created_at else '',
            'date_str': r.date.strftime('%d/%m'),
            'action': f"{r.backup_type or r.record_type} run",
            'database': r.database,
            'status': r.outcome,
            'status_class': 'success' if r.outcome == 'Success' else 'danger'
        })
    # Incidents
    recent_incidents = IncidentRegister.objects.all().order_by('-created_at')
    if selected_db != 'All Databases':
        recent_incidents = recent_incidents.filter(database__iexact=selected_db)
    recent_incidents = recent_incidents[:8]
    for r in recent_incidents:
        action_str = "Incident logged" if r.status in ['Open', 'In Progress'] else f"Incident {r.status.lower()}"
        activities.append({
            'sort_time': r.created_at,
            'time_str': r.created_at.strftime('%H:%M') if r.created_at else '',
            'date_str': r.date.strftime('%d/%m'),
            'action': action_str,
            'database': r.database,
            'status': r.status,
            'status_class': 'danger' if r.status in ['Open', 'In Progress'] else 'success'
        })
    # Replication
    recent_rep = ReplicationMonitor.objects.all().order_by('-date', '-created_at')
    if selected_db != 'All Databases':
        recent_rep = recent_rep.filter(database__iexact=selected_db)
    recent_rep = recent_rep[:8]
    for r in recent_rep:
        status_val = 'Healthy' if (r.severity != 'Critical' and r.io_thread == 'Yes' and r.sql_thread == 'Yes') else 'Issue'
        sort_time = r.created_at if r.created_at else timezone.now().replace(year=r.date.year, month=r.date.month, day=r.date.day, hour=0, minute=0, second=0, microsecond=0)
        activities.append({
            'sort_time': sort_time,
            'time_str': r.created_at.strftime('%H:%M') if r.created_at else '',
            'date_str': r.date.strftime('%d/%m'),
            'action': "Replication check",
            'database': r.database,
            'status': status_val,
            'status_class': 'success' if status_val == 'Healthy' else 'warning'
        })
        
    activities.sort(key=lambda x: x['sort_time'], reverse=True)
    recent_activity = activities[:8]

    # Calculate Default Lede narrative for modal preview
    all_within_sla_dashboard = True
    total_incidents_all_dashboard = 0
    max_incidents_db_dashboard = None
    max_incidents_val_dashboard = -1
    
    mttr_values_dashboard = []
    
    for item in filtered_dashboard_data:
        if item['availability_status'] != 'Meets SLA':
            all_within_sla_dashboard = False
            
        inc_count = item['total_incidents']
        if isinstance(inc_count, int):
            total_incidents_all_dashboard += inc_count
            if inc_count > max_incidents_val_dashboard:
                max_incidents_val_dashboard = inc_count
                max_incidents_db_dashboard = item['database']
                
        mttr_min = item['mttr_mins']
        if isinstance(mttr_min, (int, float)):
            mttr_values_dashboard.append(mttr_min)
            
    avg_mttr_dashboard = f"{round((sum(mttr_values_dashboard)/len(mttr_values_dashboard))/60, 2)} hrs" if mttr_values_dashboard else '—'

    if selected_db != 'All Databases':
        if all_within_sla_dashboard:
            lede_prefix = f"The {selected_db} production database held within SLA in {reporting_month.strftime('%B %Y')}."
        else:
            lede_prefix = f"The {selected_db} production database did not meet the SLA target in {reporting_month.strftime('%B %Y')}."

        if total_incidents_all_dashboard > 0:
            lede_suffix = f"A total of {total_incidents_all_dashboard} incident(s) were logged, with a mean time to resolution of {avg_mttr_dashboard}."
        else:
            lede_suffix = "No incidents or service interruptions were logged during this period."
    else:
        if all_within_sla_dashboard:
            lede_prefix = f"All three production databases held within SLA in {reporting_month.strftime('%B %Y')}."
        else:
            lede_prefix = f"Not all production databases met the SLA target in {reporting_month.strftime('%B %Y')}."

        if max_incidents_db_dashboard and max_incidents_val_dashboard > 0:
            others_combined = total_incidents_all_dashboard - max_incidents_val_dashboard
            if max_incidents_val_dashboard > others_combined:
                compare_str = "more than the other systems combined"
            else:
                compare_str = "more than any other system"
            lede_suffix = f"The one thing worth a second look: {max_incidents_db_dashboard} logged {max_incidents_val_dashboard} incidents — {compare_str} — even though its uptime never slipped."
        else:
            lede_suffix = "No significant incident trends were logged during this period."
            
    default_lede_text = f"{lede_prefix} {lede_suffix}"

    can_view_db_dashboard = (
        request.user.is_admin
        or request.user.is_superuser
        or request.user.has_perm('api.view_databasedashboard')
    )
    can_view_infra_dashboard = (
        request.user.is_admin
        or request.user.is_superuser
        or request.user.has_perm('api.view_infrastructuredashboard')
    )

    context = {
        'incident_count': row[0],
        'replication_count': row[1],
        'backup_count': row[2],
        'server_health_count': row[3],
        'maintenance_count': row[4],
        'capacity_count': row[5],
        'is_admin': request.user.is_admin,
        'can_view_db_dashboard': can_view_db_dashboard,
        'can_view_infra_dashboard': can_view_infra_dashboard,
        'dashboard_data': dashboard_data,
        'database_detail': database_detail,
        'selected_db': selected_db,
        'reporting_month': reporting_month.strftime('%Y-%m'),
        'default_lede_text': default_lede_text,
        'target_sla_val': target_sla_val,
        
        # New Redesign KPIs
        'kpi_availability': kpi_availability,
        'kpi_availability_change': kpi_availability_change,
        'kpi_sla_compliance': kpi_sla_compliance,
        'kpi_incidents_total': kpi_incidents_total,
        'kpi_incidents_open': kpi_incidents_open,
        'kpi_backup_success': kpi_backup_success,
        'kpi_backup_failed': kpi_backup_failed,
        'kpi_replication_status': kpi_replication_status,
        'trend_months': trend_months,
        'trend_recent_avg': trend_recent_avg,
        'trend_polyline_points': trend_polyline_points,
        'trend_area_points': trend_area_points,
        'sla_y_coord': sla_y_coord,
        'y_min': y_min,
        'y_max': y_max,
        'attention_items': attention_items,
        'recent_activity': recent_activity,
    }
    selected_node = request.GET.get('node_select', 'All Nodes')
    context.update(_build_node_dashboard_context(selected_node=selected_node, reporting_month=reporting_month))
    return render(request, 'dashboard.html', context)

@login_required
def dashboard_print(request):
    if not (request.user.is_admin or request.user.is_superuser or request.user.has_perm('api.view_databasedashboard')):
        messages.error(request, 'You do not have permission to view or export the Database Operations report.')
        return redirect('dashboard')

    if request.method == 'POST':
        month_str = request.POST.get('month', '')
        selected_db = request.POST.get('database_select', 'All Databases')
        summary_narrative = request.POST.get('summary_narrative', '').strip()
        download_mode = request.POST.get('download', 'false')
    else:
        month_str = request.GET.get('month', '')
        selected_db = request.GET.get('database_select', 'All Databases')
        summary_narrative = request.GET.get('summary_narrative', '').strip()
        download_mode = request.GET.get('download', 'false')

    if selected_db in ['MKInsight', 'Pentana']:
        selected_db = 'MS SQL Server'

    if month_str:
        try:
            reporting_month = datetime.strptime(month_str, '%Y-%m').date()
        except ValueError:
            reporting_month = timezone.now().date().replace(day=1)
    else:
        reporting_month = timezone.now().date().replace(day=1)

    dashboard_data, _ = _get_dashboard_data(reporting_month)
    
    # Filter dashboard_data by selected_db if a specific database is selected
    if selected_db != 'All Databases':
        dashboard_data = [item for item in dashboard_data if item['database'].lower() == selected_db.lower()]

    database_detail = _get_database_detail(selected_db, reporting_month, dashboard_data)
    
    # Calculate target SLA dynamically for print view
    target_sla_val = 99.95
    if selected_db != 'All Databases':
        sla_conf = SLAConfig.objects.filter(
            database__iexact=selected_db, month__year=reporting_month.year, month__month=reporting_month.month
        ).first()
        if sla_conf:
            target_sla_val = float(sla_conf.sla_percent)
        elif selected_db.lower() == 'mysql':
            target_sla_val = 99.50
    
    user_display = request.user.get_full_name()
    if not user_display:
        user_display = request.user.username.replace('.', ' ').title()

    # Dynamic KPI & Editorial Calculations
    formatted_data = []
    total_incidents_all = 0
    total_breaches_all = 0
    all_within_sla = True
    max_incidents_db = None
    max_incidents_val = -1
    
    uptimes = []
    mttr_values = []
    
    for item in dashboard_data:
        db_type = item['database']
        avail_str = item['availability']
        
        is_compliant = item['availability_status'] == 'Meets SLA'
        if not is_compliant:
            all_within_sla = False
            
        inc_count = item['total_incidents']
        if isinstance(inc_count, int):
            total_incidents_all += inc_count
            if inc_count > max_incidents_val:
                max_incidents_val = inc_count
                max_incidents_db = db_type
                
        breach_count = item.get('sla_breach_count', 0)
        if isinstance(breach_count, int):
            total_breaches_all += breach_count
            
        if avail_str and avail_str != 'No Data':
            try:
                uptimes.append(float(avail_str.replace('%', '')))
            except ValueError:
                pass
                
        mttr_min = item['mttr_mins']
        if isinstance(mttr_min, (int, float)):
            mttr_values.append(mttr_min)
            
        mttr_hrs_str = f"{round(mttr_min / 60.0, 2)} hrs" if (isinstance(mttr_min, (int, float)) and mttr_min > 0) else '—'
        
        # Calculate scaled width mapping the range [99.0%, 100.0%] to [0%, 100%]
        scaled_width = 0.0
        if avail_str and avail_str != 'No Data':
            try:
                avail_val = float(avail_str.replace('%', ''))
                if avail_val >= 99.0:
                    scaled_width = round((avail_val - 99.0) * 100.0, 2)
            except ValueError:
                pass

        target_val = item.get('target_sla_val', 99.95)
        target_scaled = round((target_val - 99.0) * 100.0, 2) if target_val >= 99.0 else 0.0

        role_desc = 'Core Enterprise ERP Suite' if db_type == 'Oracle' else ('NMS360, DMT and other in-house applications' if db_type == 'MySQL' else 'MKInsight Audit Framework')
        host_desc = 'nmsinfra.nms.go.ug (DB) & nmsebs.nms.go.ug (App)' if db_type == 'Oracle' else ('nickledbnew (Replicated to nickledbdr for DR)' if db_type == 'MySQL' else 'Windows Server Host (Standalone)')

        formatted_data.append({
            'database': db_type,
            'code_name': 'nmsinfra / nmsebs' if db_type == 'Oracle' else ('nickledbnew' if db_type == 'MySQL' else 'Pentana'),
            'role_desc': role_desc,
            'host_desc': host_desc,
            'availability': avail_str,
            'availability_num': float(avail_str.replace('%', '')) if (avail_str and avail_str != 'No Data') else 0.0,
            'availability_scaled_num': scaled_width,
            'availability_status': item['availability_status'],
            'planned_downtime': f"{item['planned_downtime']} hrs" if isinstance(item['planned_downtime'], (int, float)) else item['planned_downtime'],
            'unplanned_downtime': f"{item['unplanned_downtime']} hrs" if isinstance(item['unplanned_downtime'], (int, float)) else item['unplanned_downtime'],
            'total_incidents': item['total_incidents'],
            'sla_breach_count': item['sla_breach_count'],
            'mttr_str': mttr_hrs_str,
            'backup_success': item['backup_success'],
            'restore_success': item['restore_success'],
            'replication_status': item['replication_status'],
            'target_sla_val': target_val,
            'target_sla_scaled': target_scaled,
            
            # Disk space and tablespace capacity planning values
            'database_size_gb': f"{item['database_size_gb']} GB" if isinstance(item.get('database_size_gb'), (int, float)) else item.get('database_size_gb', 'No Data'),
            'available_capacity_gb': f"{item['available_capacity_gb']} GB" if isinstance(item.get('available_capacity_gb'), (int, float)) else item.get('available_capacity_gb', 'No Data'),
            'disk_used_percent': f"{item['disk_used_percent']}%" if isinstance(item.get('disk_used_percent'), (int, float)) else item.get('disk_used_percent', 'No Data'),
            'tablespace_used_percent': f"{item['tablespace_used_percent']}%" if isinstance(item.get('tablespace_used_percent'), (int, float)) else item.get('tablespace_used_percent', 'No Data'),
        })

    avg_uptime = f"{round(sum(uptimes)/len(uptimes), 2)}%" if uptimes else 'No Data'
    avg_mttr = f"{round((sum(mttr_values)/len(mttr_values))/60, 2)} hrs" if mttr_values else '—'

    # Build Lede Text
    if selected_db != 'All Databases':
        if all_within_sla:
            lede_prefix = f"The {selected_db} production database held within SLA in {reporting_month.strftime('%B %Y')}."
        else:
            lede_prefix = f"The {selected_db} production database did not meet the SLA target in {reporting_month.strftime('%B %Y')}."

        if total_incidents_all > 0:
            lede_suffix = f"A total of {total_incidents_all} incident(s) were logged, with a mean time to resolution of {avg_mttr}."
        else:
            lede_suffix = "No incidents or service interruptions were logged during this period."
    else:
        if all_within_sla:
            lede_prefix = f"All three production databases held within SLA in {reporting_month.strftime('%B %Y')}."
        else:
            lede_prefix = f"Not all production databases met the SLA target in {reporting_month.strftime('%B %Y')}."

        if max_incidents_db and max_incidents_val > 0:
            others_combined = total_incidents_all - max_incidents_val
            if max_incidents_val > others_combined:
                compare_str = "more than the other systems combined"
            else:
                compare_str = "more than any other system"
            lede_suffix = f"The one thing worth a second look: <em>{max_incidents_db} logged {max_incidents_val} incidents</em> — {compare_str} — even though its uptime never slipped."
        else:
            lede_suffix = "No significant incident trends were logged during this period."
    
    if summary_narrative:
        lede_text = summary_narrative
    else:
        lede_text = f"{lede_prefix} {lede_suffix}"

    # Build Watch Item Text
    if selected_db != 'All Databases':
        if total_incidents_all > 0:
            watch_item_text = f"<strong>Watch item —</strong> {selected_db} logged {total_incidents_all} incident(s) during this period. Mean time to resolution was {avg_mttr}."
        else:
            watch_item_text = f"<strong>Watch item —</strong> No major watch items logged for {selected_db}. Performance is within baseline thresholds."
    else:
        if max_incidents_db and max_incidents_val > 0:
            watch_item_text = f"<strong>Watch item —</strong> {max_incidents_db}'s incident count is the outlier this month. Uptime and MTTR were both within range, so this didn't threaten SLA, but the volume is worth a root-cause review before it trends upward."
        else:
            watch_item_text = "<strong>Watch item —</strong> No major watch items logged for this period. All systems are performing within baseline thresholds."

    context = {
        'dashboard_data': formatted_data,
        'reporting_month_str': reporting_month.strftime('%B %Y'),
        'extracted_by': user_display,
        'extracted_at': timezone.localtime(timezone.now()).strftime('%d %b %Y, %H:%M'),
        'database_detail': database_detail,
        'selected_db': selected_db,
        'avg_uptime': avg_uptime,
        'total_breaches': total_breaches_all,
        'total_incidents': total_incidents_all,
        'avg_mttr': avg_mttr,
        'lede_text': lede_text,
        'watch_item_text': watch_item_text,
        'target_sla_val': target_sla_val,
        'download_mode': download_mode,
    }
    return render(request, 'dashboard_print.html', context)

@login_required
def dashboard_excel(request):
    if not (request.user.is_admin or request.user.is_superuser or request.user.has_perm('api.view_databasedashboard')):
        messages.error(request, 'You do not have permission to export the Database Operations report.')
        return redirect('dashboard')

    month_str = request.GET.get('month', '')
    if month_str:
        try:
            reporting_month = datetime.strptime(month_str, '%Y-%m').date()
        except ValueError:
            reporting_month = timezone.now().date().replace(day=1)
    else:
        reporting_month = timezone.now().date().replace(day=1)

    selected_db = request.GET.get('database_select', 'All Databases')
    if selected_db in ['MKInsight', 'Pentana']:
        selected_db = 'MS SQL Server'
    dashboard_data, _ = _get_dashboard_data(reporting_month)
    database_detail = _get_database_detail(selected_db, reporting_month, dashboard_data)
    month_display = reporting_month.strftime('%B %Y')

    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from openpyxl.utils import get_column_letter
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "DB Performance Summary"
        
        # Style Definitions
        font_title = Font(name='Segoe UI', size=14, bold=True, color='002569')
        font_header = Font(name='Segoe UI', size=11, bold=True, color='FFFFFF')
        font_body = Font(name='Segoe UI', size=10)
        font_bold = Font(name='Segoe UI', size=10, bold=True)
        
        fill_header = PatternFill(start_color='002569', end_color='002569', fill_type='solid')
        fill_even = PatternFill(start_color='F4F7FC', end_color='F4F7FC', fill_type='solid')
        
        align_center = Alignment(horizontal='center', vertical='center')
        align_left = Alignment(horizontal='left', vertical='center')
        
        border_thin = Border(
            left=Side(style='thin', color='D1D7E0'),
            right=Side(style='thin', color='D1D7E0'),
            top=Side(style='thin', color='D1D7E0'),
            bottom=Side(style='thin', color='D1D7E0')
        )
        
        # Add Title
        ws.merge_cells('A1:P1')
        if selected_db and selected_db != 'All Databases':
            ws['A1'] = f"Summary of Database Performance for {selected_db} - {month_display}"
        else:
            ws['A1'] = f"Summary of Database Performance - {month_display}"
        ws['A1'].font = font_title
        ws['A1'].alignment = align_left
        ws.row_dimensions[1].height = 30
        
        ws.append([]) # Empty row
        row_num = 3

        if selected_db == 'All Databases':
            # Add Headers
            headers = [
                "Database", "SLA Uptime", "Planned Downtime (Hrs)", "Unplanned Downtime (Hrs)",
                "Total Incidents", "SLA Breaches", "MTTR (Mins)", 
                "Backup Success %", "Restore Success %", "Replication Status"
            ]
            ws.append(headers)
            ws.row_dimensions[row_num].height = 25
            
            for col_num, header in enumerate(headers, 1):
                cell = ws.cell(row=row_num, column=col_num)
                cell.font = font_header
                cell.fill = fill_header
                cell.alignment = align_center
                cell.border = border_thin
                
            row_num += 1
            # Add Data rows
            for item in dashboard_data:
                row_data = [
                    item['database'],
                    item['availability'],
                    item['planned_downtime'],
                    item['unplanned_downtime'],
                    item['total_incidents'],
                    item['sla_breach_count'],
                    item['mttr_mins'],
                    item['backup_success'],
                    item['restore_success'],
                    item['replication_status']
                ]
                ws.append(row_data)
                ws.row_dimensions[row_num].height = 20
                
                # Apply body styles
                for col_num in range(1, 11):
                    cell = ws.cell(row=row_num, column=col_num)
                    cell.font = font_body
                    cell.border = border_thin
                    if row_num % 2 == 0:
                        cell.fill = fill_even
                    
                    if col_num == 1:
                        cell.alignment = align_left
                    else:
                        cell.alignment = align_center
                row_num += 1
                
        # Add Database Detail Table if requested
        if database_detail:
            if selected_db == 'All Databases':
                ws.append([]) # empty
                ws.append([]) # empty
                row_num += 2
                
            detail_title_row = row_num
            ws.cell(row=detail_title_row, column=1, value=f"Database Detail - {database_detail['database']}").font = Font(name='Segoe UI', size=12, bold=True, color='002569')
            ws.row_dimensions[detail_title_row].height = 25
            row_num += 1
            
            # Detail headers (16 columns)
            detail_headers = [
                "Type", "Overall Uptime", "Planned Downtime", "Unplanned Downtime",
                "Incidents", "SLA Breaches", "MTTR (Mins)", "Backup Success %",
                "Restore Success %", "Replication Status", "Last Full", "Last Inc",
                "Last DR Test", "Failed Backups", "Failed Restores", "Replication Lag"
            ]
            ws.append(detail_headers)
            ws.row_dimensions[row_num].height = 25
            
            for col_idx, header in enumerate(detail_headers, 1):
                cell = ws.cell(row=row_num, column=col_idx)
                cell.font = font_header
                cell.fill = fill_header
                cell.alignment = align_center
                cell.border = border_thin
            row_num += 1
            
            # Current Row
            current = database_detail['current']
            row_current = [
                "Current",
                current['availability'],
                f"{current['planned_downtime']} hrs" if current['planned_downtime'] != 'No Data' else 'No Data',
                f"{current['unplanned_downtime']} hrs" if current['unplanned_downtime'] != 'No Data' else 'No Data',
                current['total_incidents'],
                current['sla_breach_count'],
                f"{current['mttr_mins']} mins" if current['mttr_mins'] != 'No Data' else 'No Data',
                current['backup_success'],
                current['restore_success'],
                current['replication_status'],
                current['last_full'],
                current['last_inc'],
                current['last_dr'],
                current['failed_backups'],
                current['failed_restores'],
                current['replication_lag']
            ]
            ws.append(row_current)
            ws.row_dimensions[row_num].height = 20
            for col_idx in range(1, 17):
                cell = ws.cell(row=row_num, column=col_idx)
                cell.font = font_body
                cell.border = border_thin
                if col_idx == 1:
                    cell.alignment = align_left
                else:
                    cell.alignment = align_center
            row_num += 1
            
            # Status Row
            status = database_detail['status']
            row_status = [
                "Status",
                status['availability'],
                status['planned_downtime'],
                status['unplanned_downtime'],
                status['total_incidents'],
                "—",
                status['mttr_mins'],
                status['backup_success'],
                status['restore_success'],
                status['replication_status'],
                status['last_full'],
                status['last_inc'],
                status['last_dr'],
                status['failed_backups'],
                status['failed_restores'],
                status['replication_lag']
            ]
            ws.append(row_status)
            ws.row_dimensions[row_num].height = 20
            
            # Colors for Meets/Below SLA
            fill_meets = PatternFill(start_color='E8F5E9', end_color='E8F5E9', fill_type='solid') # soft green
            fill_below = PatternFill(start_color='FFEBEE', end_color='FFEBEE', fill_type='solid') # soft red
            font_meets = Font(name='Segoe UI', size=10, color='2E7D32', bold=True)
            font_below = Font(name='Segoe UI', size=10, color='C62828', bold=True)
            
            for col_idx in range(1, 17):
                cell = ws.cell(row=row_num, column=col_idx)
                cell.font = font_body
                cell.border = border_thin
                
                if col_idx == 1:
                    cell.alignment = align_left
                else:
                    cell.alignment = align_center
                    
                val = str(cell.value)
                if val == 'Meets SLA':
                    cell.fill = fill_meets
                    cell.font = font_meets
                elif val == 'Below SLA':
                    cell.fill = fill_below
                    cell.font = font_below
            row_num += 1

        # Add Legend / Key below table
        ws.append([]) # empty
        ws.append([]) # empty
        legend_start = row_num + 2
        
        ws.cell(row=legend_start, column=1, value="Database Key & Host Systems:").font = font_bold
        ws.cell(row=legend_start+1, column=1, value="• Oracle: nmsinfra.nms.go.ug (DB) & nmsebs.nms.go.ug (App) - Powers the core NMS ERP suite supporting finance, procurement, stores, and order management.").font = font_body
        ws.cell(row=legend_start+2, column=1, value="• MySQL: nickledbnew (replicated live to nickeldbdr) - Hosts vital collaboration and support systems including the NMS360 suite, IT ServiceDesk, DMT, and other in-house applications.").font = font_body
        ws.cell(row=legend_start+3, column=1, value="• MS SQL Server: Windows Server host - Hosts MKInsight, which runs Pentana Audit, the system used by internal auditors.").font = font_body
        
        # Adjust Column Widths
        for col in ws.columns:
            max_len = 0
            col_val = col[0].column
            col_letter = get_column_letter(col_val) if isinstance(col_val, int) else str(col_val)
            for cell in col:
                if cell.row == 1 or cell.row > legend_start:
                    continue
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)
            
        filename_prefix = f"DB_Performance_Summary_{selected_db.replace(' ', '_')}_{month_str}" if selected_db and selected_db != 'All Databases' else f"DB_Performance_Summary_{month_str}"
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="{filename_prefix}.xlsx"'
        wb.save(response)
        return response
        
    except Exception:
        import csv
        filename_prefix = f"DB_Performance_Summary_{selected_db.replace(' ', '_')}_{month_str}" if selected_db and selected_db != 'All Databases' else f"DB_Performance_Summary_{month_str}"
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename_prefix}.csv"'
        writer = csv.writer(response)
        if selected_db and selected_db != 'All Databases':
            writer.writerow([f"Summary of Database Performance for {selected_db} - {month_display}"])
        else:
            writer.writerow([f"Summary of Database Performance - {month_display}"])
        writer.writerow([])
        
        if selected_db == 'All Databases':
            writer.writerow([
                "Database", "SLA Uptime", "Planned Downtime (Hrs)", "Unplanned Downtime (Hrs)",
                "Total Incidents", "SLA Breaches", "MTTR (Mins)", 
                "Backup Success %", "Restore Success %", "Replication Status"
            ])
            for item in dashboard_data:
                writer.writerow([
                    item['database'],
                    item['availability'],
                    item['planned_downtime'],
                    item['unplanned_downtime'],
                    item['total_incidents'],
                    item['sla_breach_count'],
                    item['mttr_mins'],
                    item['backup_success'],
                    item['restore_success'],
                    item['replication_status']
                ])
                
        if database_detail:
            if selected_db == 'All Databases':
                writer.writerow([])
                writer.writerow([])
            writer.writerow([f"Database Detail - {database_detail['database']}"])
            writer.writerow([])
            
            detail_headers = [
                "Type", "Overall Uptime", "Planned Downtime", "Unplanned Downtime",
                "Incidents", "SLA Breaches", "MTTR (Mins)", "Backup Success %",
                "Restore Success %", "Replication Status", "Last Full", "Last Inc",
                "Last DR Test", "Failed Backups", "Failed Restores", "Replication Lag"
            ]
            writer.writerow(detail_headers)
            
            current = database_detail['current']
            writer.writerow([
                "Current",
                current['availability'],
                f"{current['planned_downtime']} hrs" if current['planned_downtime'] != 'No Data' else 'No Data',
                f"{current['unplanned_downtime']} hrs" if current['unplanned_downtime'] != 'No Data' else 'No Data',
                current['total_incidents'],
                current['sla_breach_count'],
                f"{current['mttr_mins']} mins" if current['mttr_mins'] != 'No Data' else 'No Data',
                current['backup_success'],
                current['restore_success'],
                current['replication_status'],
                current['last_full'],
                current['last_inc'],
                current['last_dr'],
                current['failed_backups'],
                current['failed_restores'],
                current['replication_lag']
            ])
            
            status = database_detail['status']
            writer.writerow([
                "Status",
                status['availability'],
                status['planned_downtime'],
                status['unplanned_downtime'],
                status['total_incidents'],
                "—",
                status['mttr_mins'],
                status['backup_success'],
                status['restore_success'],
                status['replication_status'],
                status['last_full'],
                status['last_inc'],
                status['last_dr'],
                status['failed_backups'],
                status['failed_restores'],
                status['replication_lag']
            ])
            
        writer.writerow([])
        writer.writerow(["Database Key & Host Systems:"])
        writer.writerow(["Oracle: nmsinfra.nms.go.ug (DB) & nmsebs.nms.go.ug (App) - Powers the core NMS ERP suite supporting finance, procurement, stores, and order management."])
        writer.writerow(["MySQL: nickledbnew (replicated live to nickeldbdr) - Hosts vital collaboration and support systems including the NMS360 suite, IT ServiceDesk, DMT, and other in-house applications."])
        writer.writerow(["MS SQL Server: Windows Server host - Hosts MKInsight, which runs Pentana Audit, the system used by internal auditors."])
        return response

@login_required
def incident_list(request):
    auto_close_incidents()
    
    if not (request.user.is_admin or request.user.has_perm('api.view_incidentregister')):
        messages.error(request, 'You do not have permission to view incident records.')
        return redirect('dashboard')
    queryset = IncidentRegister.objects.prefetch_related('change_logs', 'change_logs__changed_by').all().order_by('-date', '-start')
    
    current_db = request.GET.get('database', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    if current_db:
        queryset = queryset.filter(database=current_db)
    if start_date:
        queryset = queryset.filter(date__gte=start_date)
    if end_date:
        queryset = queryset.filter(date__lte=end_date)
        
    paginator = Paginator(queryset, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Calculate KPIs based on filtered scope/dates
    kpi_queryset = IncidentRegister.objects.all()
    if current_db:
        kpi_queryset = kpi_queryset.filter(database=current_db)
    if start_date:
        kpi_queryset = kpi_queryset.filter(date__gte=start_date)
    if end_date:
        kpi_queryset = kpi_queryset.filter(date__lte=end_date)

    kpi_active_outages = kpi_queryset.filter(status__in=['Open', 'In Progress']).count()
    kpi_resolved_count = kpi_queryset.filter(status__in=['Resolved', 'Closed']).count()
    
    from django.db.models import Sum
    kpi_downtime_hours = kpi_queryset.filter(planned=False).aggregate(total=Sum('duration_hrs'))['total'] or 0.0
    kpi_downtime_hours = round(float(kpi_downtime_hours), 1)
    
    return render(request, 'incidents/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'start_date': start_date,
        'end_date': end_date,
        'kpi_active_outages': kpi_active_outages,
        'kpi_resolved_count': kpi_resolved_count,
        'kpi_downtime_hours': kpi_downtime_hours
    })

@login_required
def incident_create(request):
    if not (request.user.is_admin or request.user.has_perm('api.add_incidentregister')):
        messages.error(request, 'You do not have permission to create incident records.')
        return redirect('incident_list')
        
    if request.method == 'POST':
        form = IncidentRegisterForm(request.POST)
        if form.is_valid():
            incident = form.save(commit=False)
            incident.created_by = request.user
            incident.save()
            action_text = 'Created'
            if incident.assigned_to:
                action_text += f' and assigned to {incident.assigned_to}'
                
            IncidentChangeLog.objects.create(
                incident=incident,
                changed_by=request.user,
                action=action_text,
                new_status=incident.status,
                assigned_to=incident.assigned_to,
                comment=form.cleaned_data.get('change_comment', '')
            )
            messages.success(request, 'Incident created successfully.')
            return redirect('incident_list')
    else:
        form = IncidentRegisterForm()
    return render(request, 'incidents/form.html', {'form': form, 'title': 'Create Incident'})

@login_required
def incident_edit(request, pk):
    incident = get_object_or_404(IncidentRegister, pk=pk)
    
    can_edit_always = request.user.is_admin
    is_creator_within_time = (incident.created_by == request.user) and ((timezone.now() - incident.created_at).total_seconds() < 300)
    
    if not (can_edit_always or is_creator_within_time):
        messages.error(request, 'You can only edit your own records within 5 minutes of creation.')
        return redirect('incident_list')
    
    if request.method == 'POST':
        old_status = incident.status
        old_assigned = incident.assigned_to
        
        form = IncidentRegisterForm(request.POST, instance=incident)
        if form.is_valid():
            incident = form.save()
            
            new_status = incident.status
            new_assigned = incident.assigned_to
            comment = form.cleaned_data.get('change_comment', '')
            
            if old_status != new_status or old_assigned != new_assigned or comment:
                action_parts = []
                if old_status != new_status:
                    action_parts.append(f"Status changed from {old_status} to {new_status}")
                if old_assigned != new_assigned:
                    if old_assigned:
                        action_parts.append(f"Escalated to {new_assigned}")
                    else:
                        action_parts.append(f"Assigned to {new_assigned}")
                
                action = " | ".join(action_parts) if action_parts else "Update Logged"
                
                IncidentChangeLog.objects.create(
                    incident=incident,
                    changed_by=request.user,
                    action=action,
                    old_status=old_status,
                    new_status=new_status,
                    assigned_to=new_assigned,
                    comment=comment
                )
            
            messages.success(request, 'Incident updated successfully.')
            return redirect('incident_list')
    else:
        form = IncidentRegisterForm(instance=incident)
    
    return render(request, 'incidents/form.html', {
        'form': form, 
        'title': 'Edit Incident',
        'can_edit': True,
        'incident': incident,
        'change_logs': incident.change_logs.all()
    })

@login_required
def replication_list(request):
    if not (request.user.is_admin or request.user.has_perm('api.view_replicationmonitor')):
        messages.error(request, 'You do not have permission to view replication records.')
        return redirect('dashboard')
    queryset = ReplicationMonitor.objects.all().order_by('-date', '-created_at')
    
    current_db = request.GET.get('database', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    
    if current_db:
        queryset = queryset.filter(database=current_db)
        
    try:
        if start_date:
            parsed_start = datetime.strptime(start_date, '%Y-%m-%d').date()
            queryset = queryset.filter(date__gte=parsed_start)
        if end_date:
            parsed_end = datetime.strptime(end_date, '%Y-%m-%d').date()
            queryset = queryset.filter(date__lte=parsed_end)
    except ValueError:
        pass
        
    paginator = Paginator(queryset, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Calculate KPIs based on filtered scope/dates
    kpi_queryset = ReplicationMonitor.objects.all()
    if current_db:
        kpi_queryset = kpi_queryset.filter(database=current_db)
    if start_date:
        kpi_queryset = kpi_queryset.filter(date__gte=start_date)
    if end_date:
        kpi_queryset = kpi_queryset.filter(date__lte=end_date)
        
    total_checks = kpi_queryset.count()
    healthy_checks = kpi_queryset.filter(status='Healthy').count()
    kpi_replication_health_rate = f"{round((healthy_checks / total_checks) * 100, 1)}%" if total_checks > 0 else 'No Data'
    kpi_replication_failed_count = kpi_queryset.filter(status='Failed').count()
    
    lag_queryset = kpi_queryset.filter(seconds_behind__isnull=False)
    kpi_max_lag = lag_queryset.order_by('-seconds_behind').first().seconds_behind if lag_queryset.exists() else 0
    
    return render(request, 'replication/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'start_date': start_date,
        'end_date': end_date,
        'kpi_replication_health_rate': kpi_replication_health_rate,
        'kpi_replication_failed_count': kpi_replication_failed_count,
        'kpi_max_lag': kpi_max_lag
    })

@login_required
def replication_create(request):
    if not (request.user.is_admin or request.user.has_perm('api.add_replicationmonitor')):
        messages.error(request, 'You do not have permission to create replication records.')
        return redirect('replication_list')

    if request.method == 'POST':
        form = ReplicationMonitorForm(request.POST)
        if form.is_valid():
            replication = form.save(commit=False)
            replication.created_by = request.user
            replication.save()
            messages.success(request, 'Replication record created successfully.')
            return redirect('replication_list')
    else:
        form = ReplicationMonitorForm()
    return render(request, 'replication/form.html', {'form': form, 'title': 'Create Replication Record'})

@login_required
def replication_edit(request, pk):
    replication = get_object_or_404(ReplicationMonitor, pk=pk)
    
    can_edit_always = request.user.is_admin
    is_creator_within_time = (replication.created_by == request.user) and ((timezone.now() - replication.created_at).total_seconds() < 300)
    
    if not (can_edit_always or is_creator_within_time):
        messages.error(request, 'You can only edit your own records within 5 minutes of creation.')
        return redirect('replication_list')
    
    if request.method == 'POST':
        form = ReplicationMonitorForm(request.POST, instance=replication)
        if form.is_valid():
            form.save()
            messages.success(request, 'Replication record updated successfully.')
            return redirect('replication_list')
    else:
        form = ReplicationMonitorForm(instance=replication)
    
    return render(request, 'replication/form.html', {
        'form': form, 
        'title': 'Edit Replication Record',
        'can_edit': True,
        'replication': replication
    })

@login_required
def backup_list(request):
    if not (request.user.is_admin or request.user.has_perm('api.view_backuprecovery')):
        messages.error(request, 'You do not have permission to view backup records.')
        return redirect('dashboard')
    queryset = BackupRecovery.objects.all().order_by('-date', '-created_at')
    
    current_db = request.GET.get('database', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    if current_db:
        queryset = queryset.filter(database=current_db)
    if start_date:
        queryset = queryset.filter(date__gte=start_date)
    if end_date:
        queryset = queryset.filter(date__lte=end_date)
        
    paginator = Paginator(queryset, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Calculate KPIs based on filtered scope/dates
    kpi_queryset = BackupRecovery.objects.all()
    if current_db:
        kpi_queryset = kpi_queryset.filter(database=current_db)
    if start_date:
        kpi_queryset = kpi_queryset.filter(date__gte=start_date)
    if end_date:
        kpi_queryset = kpi_queryset.filter(date__lte=end_date)
        
    total_backups = kpi_queryset.filter(record_type='Backup').count()
    success_backups = kpi_queryset.filter(record_type='Backup', outcome='Success').count()
    kpi_backup_success_rate = f"{round((success_backups / total_backups) * 100, 1)}%" if total_backups > 0 else 'No Data'
    kpi_backup_failed_count = kpi_queryset.filter(record_type='Backup', outcome='Failed').count()
    kpi_restore_count = kpi_queryset.filter(record_type='Recovery').count()
    
    return render(request, 'backups/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'start_date': start_date,
        'end_date': end_date,
        'kpi_backup_success_rate': kpi_backup_success_rate,
        'kpi_backup_failed_count': kpi_backup_failed_count,
        'kpi_restore_count': kpi_restore_count
    })

@login_required
def backup_create(request):
    if not (request.user.is_admin or request.user.has_perm('api.add_backuprecovery')):
        messages.error(request, 'You do not have permission to create backup records.')
        return redirect('backup_list')

    if request.method == 'POST':
        form = BackupRecoveryForm(request.POST)
        if form.is_valid():
            backup = form.save(commit=False)
            backup.created_by = request.user
            backup.save()
            messages.success(request, 'Backup record created successfully.')
            return redirect('backup_list')
    else:
        form = BackupRecoveryForm()
    return render(request, 'backups/form.html', {'form': form, 'title': 'Create Backup Record', 'can_edit': True})

@login_required
def backup_edit(request, pk):
    backup = get_object_or_404(BackupRecovery, pk=pk)
    
    can_edit_always = request.user.is_admin or request.user.is_superuser or request.user.has_perm('api.change_backuprecovery')
    is_creator_within_time = (backup.created_by == request.user) and ((timezone.now() - backup.created_at).total_seconds() < 300)
    
    if not (can_edit_always or is_creator_within_time):
        messages.error(request, 'You do not have permission to edit this backup record.')
        return redirect('backup_list')
    
    if request.method == 'POST':
        form = BackupRecoveryForm(request.POST, instance=backup)
        if form.is_valid():
            form.save()
            messages.success(request, 'Backup record updated successfully.')
            return redirect('backup_list')
    else:
        form = BackupRecoveryForm(instance=backup)
    
    return render(request, 'backups/form.html', {
        'form': form, 
        'title': 'Edit Backup Record',
        'can_edit': True,
        'backup': backup
    })

@login_required
@require_POST
def server_health_sync_prometheus(request):
    if not (request.user.is_admin or request.user.has_perm('api.add_serverhealth')):
        messages.error(request, 'You do not have permission to sync Prometheus node metrics.')
        return redirect('server_health_list')

    prom_url = request.POST.get('prometheus_url', '').strip() or None
    from etl.services.rest_client import sync_all_nodes_from_prometheus, PROMETHEUS_URL
    synced, errors = sync_all_nodes_from_prometheus(prometheus_url=prom_url)

    if synced and not errors:
        messages.success(
            request,
            f"Successfully synced live Prometheus metrics for {', '.join(f'{r.server} ({r.node_name})' for r in synced)}."
        )
    elif synced and errors:
        messages.warning(
            request,
            f"Synced {', '.join(r.server for r in synced)}, but encountered errors on: {'; '.join(errors)}"
        )
    else:
        target_url = prom_url or PROMETHEUS_URL
        messages.error(
            request,
            f"Could not reach Prometheus API at {target_url}. Verify PROMETHEUS_URL or push via prometheus_node_agent.py. ({'; '.join(errors)})"
        )
    return redirect('server_health_list')


def _build_node_dashboard_context(selected_node='All Nodes', reporting_month=None):
    from django.db.models import Avg, Q
    import math
    from etl.services.rest_client import PROMETHEUS_URL, sync_all_nodes_from_prometheus

    today_dt = timezone.now().date()
    if not reporting_month:
        target_month_dt = today_dt.replace(day=1)
    elif isinstance(reporting_month, str):
        try:
            target_month_dt = datetime.strptime(reporting_month[:7], '%Y-%m').date().replace(day=1)
        except ValueError:
            target_month_dt = today_dt.replace(day=1)
    elif hasattr(reporting_month, 'year'):
        target_month_dt = reporting_month.replace(day=1)
    else:
        target_month_dt = today_dt.replace(day=1)

    start_of_month = target_month_dt
    if start_of_month.month == 12:
        next_month = start_of_month.replace(year=start_of_month.year + 1, month=1, day=1)
    else:
        next_month = start_of_month.replace(month=start_of_month.month + 1, day=1)

    if not ServerHealth.objects.filter(date=today_dt, entered_by='Prometheus').exists():
        try:
            sync_all_nodes_from_prometheus()
        except Exception:
            pass

    node_definitions = [
        {
            'node_key': 'infra.nms.go.ug',
            'node_name': 'OLKVM',
            'display_name': 'infra.nms.go.ug',
            'role_title': 'OLKVM — Oracle Database Node',
            'hosted_systems': 'Oracle DB (infra)',
            'primary_db': 'Oracle',
            'filter_q': Q(server__icontains='infra'),
            'default_disks': [
                '/nmsddbkp (Shared NFS)',
                '/u01 (Oracle Data)',
                '/ (OS Root)',
            ],
        },
        {
            'node_key': 'ebs.nms.go.ug',
            'node_name': 'OLKVM',
            'display_name': 'ebs.nms.go.ug',
            'role_title': 'OLKVM — Oracle EBS App Node',
            'hosted_systems': 'Oracle EBS App (ebs)',
            'primary_db': 'Oracle',
            'filter_q': Q(server__icontains='ebs'),
            'default_disks': [
                '/nmsddbkp (Shared NFS)',
                '/stagearea (LVM XFS)',
                '/ (OS Root)',
            ],
        },
        {
            'node_key': 'pvenode1',
            'node_name': 'Proxmox',
            'display_name': 'pvenode1',
            'role_title': 'Proxmox Cluster Node 1',
            'hosted_systems': 'tomcatvm & Internal Systems',
            'primary_db': 'MySQL / Internal',
            'filter_q': Q(server__iexact='pvenode1'),
            'default_disks': [
                'VMSAN (Shared)',
                'local-lvm (LVM-Thin)',
                'local (Root / ISO)',
            ],
        },
        {
            'node_key': 'pvenode2',
            'node_name': 'Proxmox',
            'display_name': 'pvenode2',
            'role_title': 'Proxmox Cluster Node 2',
            'hosted_systems': 'jumphostvm & monitoringvm',
            'primary_db': 'Monitoring / Ops',
            'filter_q': Q(server__iexact='pvenode2'),
            'default_disks': [
                'VMSAN (Shared)',
                'local-lvm (LVM-Thin)',
                'local (Root / ISO)',
            ],
        },
        {
            'node_key': 'pvenode3',
            'node_name': 'Proxmox',
            'display_name': 'pvenode3',
            'role_title': 'Proxmox Cluster Node 3',
            'hosted_systems': 'databasevm (MySQL / Pentana)',
            'primary_db': 'MySQL / MS SQL Server',
            'filter_q': Q(server__iexact='pvenode3'),
            'default_disks': [
                'VMSAN (Shared)',
                'local-lvm (LVM-Thin)',
                'local (Root / ISO)',
            ],
        },
    ]

    def _disk_status(pct):
        if pct > 90:
            return 'Critical'
        elif pct > 80:
            return 'Warning'
        return 'Healthy'

    def _clean_disk_label(raw_label, default_label):
        lbl = (raw_label or default_label or '').strip()
        if lbl.startswith('storage/'):
            parts = lbl.split('/')
            lbl = parts[-1] if len(parts) >= 3 else lbl
        low = lbl.lower()
        if 'vmsan' in low:
            return 'VMSAN (Shared)'
        if 'local-lvm' in low:
            return 'local-lvm (LVM-Thin)'
        if low == 'local' or 'local (' in low:
            return 'local (Root / ISO)'
        if 'nmsddbkp' in low:
            return '/nmsddbkp (Shared NFS)'
        if '/u01' in low:
            return '/u01 (Oracle Data)'
        if 'root' in low or lbl == '/':
            return '/ (OS Root)'
        return lbl

    def _calculate_runway(free_gb, daily_burn_gb):
        if daily_burn_gb and daily_burn_gb > 0.01 and free_gb > 0:
            days = int(free_gb / daily_burn_gb)
            if days <= 30:
                return {'days': days, 'text': f"{days} days", 'badge': 'critical'}
            elif days <= 90:
                return {'days': days, 'text': f"{days} days", 'badge': 'warning'}
            elif days <= 365:
                mos = round(days / 30.4, 1)
                return {'days': days, 'text': f"{mos} mos ({days}d)", 'badge': 'healthy'}
            else:
                return {'days': days, 'text': "> 1 Year", 'badge': 'stable'}
        return {'days': 9999, 'text': "Stable", 'badge': 'stable'}

    node_overview_data = []
    for nd in node_definitions:
        # Prefer telemetry record within target month (from start of the month), fallback to latest overall
        month_scoped_qs = (
            ServerHealth.objects.filter(nd['filter_q'], date__gte=start_of_month, date__lt=next_month)
            .filter(Q(entered_by='Prometheus') | Q(disk_1_size_gb__gt=0) | Q(disk_2_size_gb__gt=0))
            .order_by('-date', '-updated_at')
        )
        latest_rec = month_scoped_qs.first()
        if not latest_rec:
            latest_rec = ServerHealth.objects.filter(nd['filter_q'], date__gte=start_of_month, date__lt=next_month).order_by('-date', '-updated_at').first()
        if not latest_rec:
            latest_rec = (
                ServerHealth.objects.filter(nd['filter_q'])
                .filter(Q(entered_by='Prometheus') | Q(disk_1_size_gb__gt=0) | Q(disk_2_size_gb__gt=0))
                .order_by('-date', '-updated_at')
                .first()
            )
        if not latest_rec:
            latest_rec = ServerHealth.objects.filter(nd['filter_q']).order_by('-date', '-updated_at').first()

        # Monthly average uptime for this specific node across the period starting from the start of the month
        month_node_qs = ServerHealth.objects.filter(
            nd['filter_q'],
            date__gte=start_of_month,
            date__lt=next_month,
        ).exclude(uptime_percent__isnull=True)

        month_up_agg = month_node_qs.aggregate(Avg('uptime_percent'))['uptime_percent__avg']

        if latest_rec:
            if month_up_agg is not None:
                up_pct = round(float(month_up_agg), 2)
            else:
                up_pct = float(latest_rec.uptime_percent if latest_rec.uptime_percent is not None else 100.0)
            up_days = float(latest_rec.uptime_days or 0.0)
            logical_cpus = int(latest_rec.logical_cpus or latest_rec.threads or (48 if nd['node_name'] == 'Proxmox' else (64 if 'nmsinfra' in nd['node_key'] else 16)))
            cpu_val = float(latest_rec.cpu_percent or 0.0)
            mem_val = float(latest_rec.memory_percent or 0.0)
            mem_tot = float(latest_rec.memory_total_gb or 0.0)
            mem_used = float(latest_rec.memory_used_gb or 0.0)
            growth_gb = float(getattr(latest_rec, 'storage_growth_gb', 0.0) or 0.0)
            growth_pct = float(latest_rec.storage_growth_pct or 0.0)

            d1_pct = float(latest_rec.disk_1_percent or 0.0)
            d1_size = float(latest_rec.disk_1_size_gb or 0.0)
            d1_used = float(latest_rec.disk_1_used_gb or 0.0)
            if d1_pct == 0.0 and d1_size == 0.0 and float(latest_rec.disk_percent or 0.0) > 0:
                d1_pct = float(latest_rec.disk_percent)

            d2_pct = float(latest_rec.disk_2_percent or 0.0)
            d2_size = float(latest_rec.disk_2_size_gb or 0.0)
            d2_used = float(latest_rec.disk_2_used_gb or 0.0)

            d3_pct = float(latest_rec.disk_3_percent or 0.0)
            d3_size = float(latest_rec.disk_3_size_gb or 0.0)
            d3_used = float(latest_rec.disk_3_used_gb or 0.0)

            # Baseline confirmed telemetry starts from October 1, 2026
            # Daily 24h Delta: compare against yesterday's run (most recent prior run)
            oct_baseline_date = datetime(2026, 10, 1).date()
            prev_run_rec = None
            if latest_rec.date > oct_baseline_date:
                prev_run_rec = (
                    ServerHealth.objects.filter(nd['filter_q'], date__lt=latest_rec.date, date__gte=oct_baseline_date)
                    .order_by('-date', '-updated_at')
                    .first()
                )

            if prev_run_rec:
                prev_d1_used = float(prev_run_rec.disk_1_used_gb or 0.0)
                prev_d2_used = float(prev_run_rec.disk_2_used_gb or 0.0)
                prev_d3_used = float(prev_run_rec.disk_3_used_gb or 0.0)
                prev_local_used = prev_d2_used + prev_d3_used
                curr_local_used = d2_used + d3_used

                d1_delta_gb = round(d1_used - prev_d1_used, 2)
                d1_delta_pct = round((d1_delta_gb / prev_d1_used) * 100.0, 2) if prev_d1_used > 0 else 0.0

                d2_delta_gb = round(d2_used - prev_d2_used, 2)
                d2_delta_pct = round((d2_delta_gb / prev_d2_used) * 100.0, 2) if prev_d2_used > 0 else 0.0

                d3_delta_gb = round(d3_used - prev_d3_used, 2)
                d3_delta_pct = round((d3_delta_gb / prev_d3_used) * 100.0, 2) if prev_d3_used > 0 else 0.0

                local_delta_gb = round(curr_local_used - prev_local_used, 2)
                local_delta_pct = round((local_delta_gb / prev_local_used) * 100.0, 2) if prev_local_used > 0 else 0.0
            else:
                d1_delta_gb = 0.0
                d1_delta_pct = 0.0
                d2_delta_gb = 0.0
                d2_delta_pct = 0.0
                d3_delta_gb = 0.0
                d3_delta_pct = 0.0
                local_delta_gb = 0.0
                local_delta_pct = 0.0

            d1_runway = _calculate_runway(max(0.0, d1_size - d1_used), d1_delta_gb)
            d2_runway = _calculate_runway(max(0.0, d2_size - d2_used), d2_delta_gb)
            d3_runway = _calculate_runway(max(0.0, d3_size - d3_used), d3_delta_gb)

            disks = [
                {
                    'slot': 1,
                    'type': _clean_disk_label(latest_rec.disk_1_type, nd['default_disks'][0]),
                    'size_gb': round(d1_size, 2),
                    'used_gb': round(d1_used, 2),
                    'free_gb': round(max(0.0, d1_size - d1_used), 2),
                    'percent': round(d1_pct, 2),
                    'status': _disk_status(d1_pct),
                    'is_shared': True,
                    'delta_24h_gb': d1_delta_gb,
                    'delta_24h_pct': d1_delta_pct,
                    'runway_days': d1_runway['days'],
                    'runway_text': d1_runway['text'],
                    'runway_badge': d1_runway['badge'],
                },
                {
                    'slot': 2,
                    'type': _clean_disk_label(latest_rec.disk_2_type, nd['default_disks'][1]),
                    'size_gb': round(d2_size, 2),
                    'used_gb': round(d2_used, 2),
                    'free_gb': round(max(0.0, d2_size - d2_used), 2),
                    'percent': round(d2_pct, 2),
                    'status': _disk_status(d2_pct),
                    'is_shared': False,
                    'delta_24h_gb': d2_delta_gb,
                    'delta_24h_pct': d2_delta_pct,
                    'runway_days': d2_runway['days'],
                    'runway_text': d2_runway['text'],
                    'runway_badge': d2_runway['badge'],
                },
                {
                    'slot': 3,
                    'type': _clean_disk_label(latest_rec.disk_3_type, nd['default_disks'][2]),
                    'size_gb': round(d3_size, 2),
                    'used_gb': round(d3_used, 2),
                    'free_gb': round(max(0.0, d3_size - d3_used), 2),
                    'percent': round(d3_pct, 2),
                    'status': _disk_status(d3_pct),
                    'is_shared': False,
                    'delta_24h_gb': d3_delta_gb,
                    'delta_24h_pct': d3_delta_pct,
                    'runway_days': d3_runway['days'],
                    'runway_text': d3_runway['text'],
                    'runway_badge': d3_runway['badge'],
                },
            ]

            tot_cap = round(sum(d['size_gb'] for d in disks), 2)
            tot_used = round(sum(d['used_gb'] for d in disks), 2)
            tot_free = round(max(0.0, tot_cap - tot_used), 2)
            local_cap = round(sum(d['size_gb'] for d in disks if not d['is_shared']), 2)
            local_used = round(sum(d['used_gb'] for d in disks if not d['is_shared']), 2)
            local_free = round(max(0.0, local_cap - local_used), 2)
            local_util_pct = round((local_used / local_cap) * 100.0, 2) if local_cap > 0 else 0.0
            peak_disk = round(max([float(latest_rec.disk_percent or 0.0)] + [d['percent'] for d in disks]), 2)

            node_runway = _calculate_runway(local_free, local_delta_gb)

            clean_hosted = latest_rec.hosted_systems or nd['hosted_systems']
            import re
            clean_hosted = re.sub(r'\s*-\s*\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', '', clean_hosted)
            clean_hosted = re.sub(r'\s*\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '', clean_hosted)
            clean_hosted = clean_hosted.replace('nmsinfra', 'infra').replace('nmsebs', 'ebs')
            clean_hosted = re.sub(r'\(\s*\)', '', clean_hosted).strip(' -()') or nd['hosted_systems']

            srv_clean = latest_rec.server or nd['display_name']
            if srv_clean.startswith('nmsinfra'):
                srv_clean = srv_clean.replace('nmsinfra', 'infra', 1)
            elif srv_clean.startswith('nmsebs'):
                srv_clean = srv_clean.replace('nmsebs', 'ebs', 1)

            node_lbl = "OLKVM" if nd['node_name'] == 'OLKVM' else f"{nd['node_name']} — {nd['display_name']}"
            node_overview_data.append({
                'node_key': nd['node_key'],
                'node_name': nd['node_name'],
                'display_name': nd['display_name'],
                'full_node_label': node_lbl,
                'role_title': nd['role_title'],
                'hosted_systems': clean_hosted,
                'primary_db': nd['primary_db'],
                'server': srv_clean,
                'has_data': True,
                'uptime_percent': round(up_pct, 2),
                'uptime_str': f"{round(up_pct, 2)}%",
                'uptime_days': round(up_days, 1),
                'uptime_status': 'Meets SLA' if up_pct >= 99.90 else ('Warning' if up_pct >= 99.0 else 'Below SLA'),
                'logical_cpus': logical_cpus,
                'cpu_percent': round(cpu_val, 2),
                'memory_percent': round(mem_val, 2),
                'memory_total_gb': round(mem_tot, 2),
                'memory_used_gb': round(mem_used, 2),
                'disk_percent': peak_disk,
                'storage_growth_gb': round(local_delta_gb, 2),
                'storage_growth_pct': round(local_delta_pct, 2),
                'delta_24h_gb': round(local_delta_gb, 2),
                'delta_24h_pct': round(local_delta_pct, 2),
                'runway_days': node_runway['days'],
                'runway_text': node_runway['text'],
                'runway_badge': node_runway['badge'],
                'disks': disks,
                'disk_1': disks[0],
                'disk_2': disks[1],
                'disk_3': disks[2],
                'total_disk_capacity_gb': tot_cap,
                'total_disk_used_gb': tot_used,
                'total_disk_free_gb': tot_free,
                'local_disk_capacity_gb': local_cap,
                'local_disk_used_gb': local_used,
                'local_disk_free_gb': local_free,
                'local_disk_util_pct': local_util_pct,
                'total_disk_used_pct': round((tot_used / tot_cap * 100.0), 1) if tot_cap > 0 else peak_disk,
                'connections': latest_rec.connections,
                'threads': latest_rec.threads,
                'slow_queries': latest_rec.slow_queries,
                'status': latest_rec.status,
                'entered_by': latest_rec.entered_by,
                'updated_at': latest_rec.updated_at,
                'date': latest_rec.date,
            })
        else:
            empty_disks = [
                {'slot': i + 1, 'type': _clean_disk_label(nd['default_disks'][i], nd['default_disks'][i]), 'size_gb': 0.0, 'used_gb': 0.0, 'free_gb': 0.0, 'percent': 0.0, 'status': 'No Data', 'is_shared': i == 0}
                for i in range(3)
            ]
            node_lbl = "OLKVM" if nd['node_name'] == 'OLKVM' else f"{nd['node_name']} — {nd['display_name']}"
            node_overview_data.append({
                'node_key': nd['node_key'],
                'node_name': nd['node_name'],
                'display_name': nd['display_name'],
                'full_node_label': node_lbl,
                'role_title': nd['role_title'],
                'hosted_systems': nd['hosted_systems'],
                'primary_db': nd['primary_db'],
                'server': nd['display_name'],
                'has_data': False,
                'uptime_percent': 0.0,
                'uptime_str': 'No Data',
                'uptime_days': 0.0,
                'uptime_status': 'N/A',
                'logical_cpus': 48 if nd['node_name'] == 'Proxmox' else (64 if 'nmsinfra' in nd['node_key'] else 16),
                'cpu_percent': 0.0,
                'memory_percent': 0.0,
                'memory_total_gb': 0.0,
                'memory_used_gb': 0.0,
                'disk_percent': 0.0,
                'storage_growth_gb': 0.0,
                'storage_growth_pct': 0.0,
                'disks': empty_disks,
                'disk_1': empty_disks[0],
                'disk_2': empty_disks[1],
                'disk_3': empty_disks[2],
                'total_disk_capacity_gb': 0.0,
                'total_disk_used_gb': 0.0,
                'total_disk_free_gb': 0.0,
                'local_disk_capacity_gb': 0.0,
                'local_disk_used_gb': 0.0,
                'local_disk_free_gb': 0.0,
                'local_disk_util_pct': 0.0,
                'total_disk_used_pct': 0.0,
                'connections': 0,
                'threads': 0,
                'slow_queries': 0,
                'status': 'No Data',
                'entered_by': 'Prometheus',
                'updated_at': None,
                'date': None,
            })

    if selected_node == 'All Nodes':
        filtered_nodes = node_overview_data
    elif selected_node in ('OLKVM', 'Proxmox'):
        filtered_nodes = [n for n in node_overview_data if n['node_name'].lower() == selected_node.lower()]
    else:
        filtered_nodes = [n for n in node_overview_data if n['node_key'].lower() == selected_node.lower()]

    scoped_node_detail = filtered_nodes[0] if (len(filtered_nodes) == 1) else None

    shared_storage_records = []
    seen_shared = set()
    for n in filtered_nodes:
        if not n['has_data'] or n['node_name'] in seen_shared:
            continue
        seen_shared.add(n['node_name'])
        d1 = n['disk_1']
        if d1['size_gb'] > 0:
            shared_storage_records.append({
                'cluster': n['node_name'],
                'storage_id': 'VMSAN' if n['node_name'] == 'Proxmox' else '/nmsddbkp',
                'type_label': d1['type'],
                'total_gb': d1['size_gb'],
                'total_tb': round(d1['size_gb'] / 1024.0, 2),
                'used_gb': d1['used_gb'],
                'free_gb': d1['free_gb'],
                'percent': d1['percent'],
                'growth_gb': d1.get('delta_24h_gb', 0.0),
                'growth_pct': d1.get('delta_24h_pct', 0.0),
                'delta_24h_gb': d1.get('delta_24h_gb', 0.0),
                'delta_24h_pct': d1.get('delta_24h_pct', 0.0),
                'runway_days': d1.get('runway_days', 9999),
                'runway_text': d1.get('runway_text', 'Stable'),
                'runway_badge': d1.get('runway_badge', 'stable'),
                'status': d1['status'],
                'date': n['date'] or today_dt,
                'updated_at': n.get('updated_at'),
            })

    # Build capacity records from the start of the month across the reporting period
    month_cap_filter = Q(date__gte=start_of_month, date__lt=next_month)
    if selected_node == 'OLKVM':
        month_cap_filter &= (Q(node_name__iexact='OLKVM') | Q(server__icontains='infra') | Q(server__icontains='ebs'))
    elif selected_node == 'Proxmox':
        month_cap_filter &= (Q(node_name__iexact='Proxmox') | Q(server__icontains='pvenode'))
    elif selected_node != 'All Nodes':
        month_cap_filter &= Q(server__iexact=selected_node)

    hist_month_qs = (
        ServerHealth.objects.filter(month_cap_filter)
        .filter(Q(entered_by='Prometheus') | Q(disk_1_size_gb__gt=0) | Q(disk_2_size_gb__gt=0))
        .order_by('-date', '-updated_at')
    )

    node_capacity_records = []

    # Collect distinct (date, server) latest runs
    seen_day_node = set()
    daily_runs = []
    for hr in hist_month_qs:
        day_key = (hr.date, (hr.server or '').lower())
        if day_key not in seen_day_node:
            seen_day_node.add(day_key)
            daily_runs.append(hr)

    if daily_runs:
        seen_shared_days = set()
        for hr in daily_runs:
            srv_lower = (hr.server or '').lower()
            nd_m = next((d for d in node_definitions if (d['node_key'].lower() in srv_lower) or (srv_lower in d['node_key'].lower()) or (d['node_name'] == 'OLKVM' and any(k in srv_lower for k in ['infra', 'ebs']))), None)
            node_lbl = nd_m['node_name'] if nd_m else (hr.node_name or 'Node')
            disp_lbl = nd_m['display_name'] if nd_m else hr.server
            host_lbl = nd_m['hosted_systems'] if nd_m else hr.hosted_systems
            defs_disks = nd_m['default_disks'] if nd_m else ['Disk 1', 'Disk 2', 'Disk 3']

            filter_node_q = nd_m['filter_q'] if nd_m else Q(server=hr.server)
            prev_hr = (
                ServerHealth.objects.filter(filter_node_q, date__lt=hr.date, date__gte=oct_baseline_date)
                .order_by('-date', '-updated_at')
                .first()
            )

            # Disk 2 (LVM)
            d2_sz = float(hr.disk_2_size_gb or 0.0)
            d2_us = float(hr.disk_2_used_gb or 0.0)
            d2_fr = max(0.0, d2_sz - d2_us)
            d2_pct = float(hr.disk_2_percent or 0.0)
            prev_d2 = float(prev_hr.disk_2_used_gb or 0.0) if prev_hr else 0.0
            d2_delta = round(d2_us - prev_d2, 2) if prev_hr else 0.0
            d2_delta_p = round((d2_delta / prev_d2) * 100.0, 2) if (prev_hr and prev_d2 > 0) else 0.0
            d2_rw = _calculate_runway(d2_fr, d2_delta)

            if d2_sz > 0:
                node_capacity_records.append({
                    'month': hr.date,
                    'date': hr.date,
                    'updated_at': hr.updated_at,
                    'node_key': hr.server,
                    'node_name': node_lbl,
                    'display_name': disp_lbl,
                    'hosted_systems': host_lbl,
                    'storage_tier': _clean_disk_label(hr.disk_2_type, defs_disks[1]),
                    'capacity_gb': round(d2_sz, 2),
                    'used_gb': round(d2_us, 2),
                    'free_gb': round(d2_fr, 2),
                    'used_percent': round(d2_pct, 2),
                    'growth_gb': d2_delta,
                    'growth_percent': d2_delta_p,
                    'delta_24h_gb': d2_delta,
                    'delta_24h_percent': d2_delta_p,
                    'runway_days': d2_rw['days'],
                    'runway_text': d2_rw['text'],
                    'runway_badge': d2_rw['badge'],
                    'has_data': True,
                    'is_shared': False,
                })

            # Disk 3 (Local)
            d3_sz = float(hr.disk_3_size_gb or 0.0)
            d3_us = float(hr.disk_3_used_gb or 0.0)
            d3_fr = max(0.0, d3_sz - d3_us)
            d3_pct = float(hr.disk_3_percent or 0.0)
            prev_d3 = float(prev_hr.disk_3_used_gb or 0.0) if prev_hr else 0.0
            d3_delta = round(d3_us - prev_d3, 2) if prev_hr else 0.0
            d3_delta_p = round((d3_delta / prev_d3) * 100.0, 2) if (prev_hr and prev_d3 > 0) else 0.0
            d3_rw = _calculate_runway(d3_fr, d3_delta)

            if d3_sz > 0:
                node_capacity_records.append({
                    'month': hr.date,
                    'date': hr.date,
                    'updated_at': hr.updated_at,
                    'node_key': hr.server,
                    'node_name': node_lbl,
                    'display_name': disp_lbl,
                    'hosted_systems': host_lbl,
                    'storage_tier': _clean_disk_label(hr.disk_3_type, defs_disks[2]),
                    'capacity_gb': round(d3_sz, 2),
                    'used_gb': round(d3_us, 2),
                    'free_gb': round(d3_fr, 2),
                    'used_percent': round(d3_pct, 2),
                    'growth_gb': d3_delta,
                    'growth_percent': d3_delta_p,
                    'delta_24h_gb': d3_delta,
                    'delta_24h_percent': d3_delta_p,
                    'runway_days': d3_rw['days'],
                    'runway_text': d3_rw['text'],
                    'runway_badge': d3_rw['badge'],
                    'has_data': True,
                    'is_shared': False,
                })

            # Disk 1 (Shared) - Dedup by (date, cluster)
            shared_key = (hr.date, node_lbl)
            if shared_key not in seen_shared_days and float(hr.disk_1_size_gb or 0.0) > 0:
                seen_shared_days.add(shared_key)
                d1_sz = float(hr.disk_1_size_gb)
                d1_us = float(hr.disk_1_used_gb or 0.0)
                d1_fr = max(0.0, d1_sz - d1_us)
                d1_pct = float(hr.disk_1_percent or 0.0)
                prev_d1 = float(prev_hr.disk_1_used_gb or 0.0) if prev_hr else 0.0
                d1_delta = round(d1_us - prev_d1, 2) if prev_hr else 0.0
                d1_delta_p = round((d1_delta / prev_d1) * 100.0, 2) if (prev_hr and prev_d1 > 0) else 0.0
                d1_rw = _calculate_runway(d1_fr, d1_delta)

                storage_id = 'VMSAN' if node_lbl == 'Proxmox' else '/nmsddbkp'
                node_capacity_records.append({
                    'month': hr.date,
                    'date': hr.date,
                    'updated_at': hr.updated_at,
                    'node_key': node_lbl,
                    'node_name': node_lbl,
                    'display_name': f"{node_lbl} Shared",
                    'hosted_systems': storage_id,
                    'storage_tier': _clean_disk_label(hr.disk_1_type, defs_disks[0]),
                    'capacity_gb': round(d1_sz, 2),
                    'used_gb': round(d1_us, 2),
                    'free_gb': round(d1_fr, 2),
                    'used_percent': round(d1_pct, 2),
                    'growth_gb': d1_delta,
                    'growth_percent': d1_delta_p,
                    'delta_24h_gb': d1_delta,
                    'delta_24h_percent': d1_delta_p,
                    'runway_days': d1_rw['days'],
                    'runway_text': d1_rw['text'],
                    'runway_badge': d1_rw['badge'],
                    'has_data': True,
                    'is_shared': True,
                })
        node_capacity_records.sort(key=lambda r: (r['date'] or today_dt, r['is_shared'], r['node_name']), reverse=True)
    else:
        for n in filtered_nodes:
            rec_month = (n['date'] or today_dt)
            rec_date = n.get('date') or today_dt
            rec_updated = n.get('updated_at')
            for d in (n['disk_2'], n['disk_3']):
                node_capacity_records.append({
                    'month': rec_month,
                    'date': rec_date,
                    'updated_at': rec_updated,
                    'node_key': n['node_key'],
                    'node_name': n['node_name'],
                    'display_name': n['display_name'],
                    'hosted_systems': n['hosted_systems'],
                    'storage_tier': d['type'],
                    'capacity_gb': d['size_gb'],
                    'used_gb': d['used_gb'],
                    'free_gb': d['free_gb'],
                    'used_percent': d['percent'],
                    'growth_gb': d.get('delta_24h_gb', 0.0),
                    'growth_percent': d.get('delta_24h_pct', 0.0),
                    'delta_24h_gb': d.get('delta_24h_gb', 0.0),
                    'delta_24h_percent': d.get('delta_24h_pct', 0.0),
                    'runway_days': d.get('runway_days', 9999),
                    'runway_text': d.get('runway_text', 'Stable'),
                    'runway_badge': d.get('runway_badge', 'stable'),
                    'has_data': n['has_data'],
                    'is_shared': False,
                })
        for sr in shared_storage_records:
            node_capacity_records.append({
                'month': sr['date'],
                'date': sr['date'],
                'updated_at': sr.get('updated_at'),
                'node_key': sr['cluster'],
                'node_name': sr['cluster'],
                'display_name': f"{sr['cluster']} Shared",
                'hosted_systems': sr['storage_id'],
                'storage_tier': sr['type_label'],
                'capacity_gb': sr['total_gb'],
                'used_gb': sr['used_gb'],
                'free_gb': sr['free_gb'],
                'used_percent': sr['percent'],
                'growth_gb': sr['delta_24h_gb'],
                'growth_percent': sr['delta_24h_pct'],
                'delta_24h_gb': sr['delta_24h_gb'],
                'delta_24h_percent': sr['delta_24h_pct'],
                'runway_days': sr['runway_days'],
                'runway_text': sr['runway_text'],
                'runway_badge': sr['runway_badge'],
                'has_data': True,
                'is_shared': True,
            })

    active_nodes = [n for n in filtered_nodes if n['has_data']]
    if active_nodes:
        avg_node_up = round(sum(n['uptime_percent'] for n in active_nodes) / len(active_nodes), 2)
        kpi_node_uptime = f"{avg_node_up}%"
        healthy_nodes = sum(1 for n in active_nodes if n['status'] == 'Healthy')
        kpi_nodes_healthy = f"{healthy_nodes}/{len(filtered_nodes)} Healthy"
    else:
        kpi_node_uptime = "No Data"
        kpi_nodes_healthy = f"0/{len(filtered_nodes)} Synced"

    kpi_total_logical_cpus = sum(n['logical_cpus'] for n in filtered_nodes)
    kpi_total_ram_gb = round(sum(n['memory_total_gb'] for n in filtered_nodes), 1)
    kpi_used_ram_gb = round(sum(n['memory_used_gb'] for n in filtered_nodes), 1)

    seen_shared_clusters = set()
    dedup_total_gb = 0.0
    dedup_used_gb = 0.0
    for n in filtered_nodes:
        if not n['has_data']:
            continue
        dedup_total_gb += n['disk_2']['size_gb'] + n['disk_3']['size_gb']
        dedup_used_gb += n['disk_2']['used_gb'] + n['disk_3']['used_gb']
        if n['node_name'] not in seen_shared_clusters:
            seen_shared_clusters.add(n['node_name'])
            dedup_total_gb += n['disk_1']['size_gb']
            dedup_used_gb += n['disk_1']['used_gb']

    kpi_total_storage_gb = round(dedup_total_gb, 1)
    kpi_used_storage_gb = round(dedup_used_gb, 1)
    kpi_free_storage_gb = round(max(0.0, kpi_total_storage_gb - kpi_used_storage_gb), 1)
    kpi_storage_used_pct = (
        round((kpi_used_storage_gb / kpi_total_storage_gb) * 100.0, 1)
        if kpi_total_storage_gb > 0
        else (round(sum(n['disk_percent'] for n in active_nodes) / len(active_nodes), 1) if active_nodes else 0.0)
    )

    today_date = timezone.now().date().replace(day=1)
    target_node_sla = 99.90
    node_trend_months = []

    for i in range(5, -1, -1):
        m_year = today_date.year
        m_month = today_date.month - i
        while m_month <= 0:
            m_month += 12
            m_year -= 1
        t_month = today_date.replace(year=m_year, month=m_month)

        month_qs = ServerHealth.objects.filter(date__year=t_month.year, date__month=t_month.month)
        if selected_node == 'OLKVM':
            month_qs = month_qs.filter(
                Q(node_name__iexact='OLKVM')
                | Q(server__icontains='infra')
                | Q(server__icontains='ebs')
                | Q(server__icontains='nmsinfra')
                | Q(server__icontains='nmsebs')
            )
        elif selected_node == 'Proxmox':
            month_qs = month_qs.filter(Q(node_name__iexact='Proxmox') | Q(server__icontains='pvenode'))
        elif selected_node != 'All Nodes':
            month_qs = month_qs.filter(server__iexact=selected_node)

        if t_month == target_month_dt and active_nodes:
            val = avg_node_up
            has_m_data = True
        elif t_month.year < 2026 or (t_month.year == 2026 and t_month.month < 10):
            # Prior to October 2026, no confirmed telemetry was commissioned
            val = 0.0
            has_m_data = False
        elif month_qs.exists():
            prom_qs = month_qs.filter(entered_by='Prometheus')
            eff_qs = prom_qs if prom_qs.exists() else month_qs
            avg_up = eff_qs.aggregate(Avg('uptime_percent'))['uptime_percent__avg']
            val = round(float(avg_up), 2) if avg_up is not None else 100.0
            has_m_data = True
        else:
            val = 0.0
            has_m_data = False

        node_trend_months.append({
            'name': t_month.strftime('%b'),
            'value': val,
            'has_data': has_m_data,
        })

    valid_vals = [m['value'] for m in node_trend_months if m['has_data'] and m['value'] > 0.0]
    node_trend_recent_avg = round(sum(valid_vals) / len(valid_vals), 2) if valid_vals else 100.0

    min_v = min((valid_vals or [100.0]) + [target_node_sla])
    y_min = math.floor(min_v - 0.2) if min_v < 98.0 else 98.0
    y_max = 100.0
    y_range = max(0.1, y_max - y_min)

    pts = []
    for idx, m in enumerate(node_trend_months):
        x = 42 + idx * 63
        m['x'] = x
        if m['has_data'] and m['value'] > 0.0:
            y = 22 + (100.0 - m['value']) * 96 / y_range
            pts.append(f"{x},{y}")
            m['y'] = y
            m['text_y'] = y - 7
        else:
            m['y'] = 118
            m['text_y'] = 110

    node_trend_polyline_points = " ".join(pts) if len(pts) > 1 else ""
    node_trend_area_points = ""
    if len(pts) > 1:
        first_pt = pts[0].split(',')
        last_pt = pts[-1].split(',')
        node_trend_area_points = f"{first_pt[0]},118 {node_trend_polyline_points} {last_pt[0]},118"
    node_sla_y_coord = 22 + (100.0 - target_node_sla) * 96 / y_range

    node_attention_items = []
    for n in filtered_nodes:
        display_label = f"OLKVM ({n['hosted_systems']})" if n['node_name'] == 'OLKVM' else f"Proxmox ({n['display_name']})"
        if not n['has_data']:
            node_attention_items.append({
                'type': 'warning',
                'message': f"<strong>{display_label}:</strong> No Prometheus telemetry synced yet."
            })
            continue

        if n['uptime_percent'] < target_node_sla:
            node_attention_items.append({
                'type': 'danger',
                'message': f"<strong>{display_label} Uptime:</strong> Node availability ({n['uptime_percent']}%) is below the {target_node_sla}% SLA target."
            })

        if n['cpu_percent'] > 90:
            node_attention_items.append({
                'type': 'danger',
                'message': f"<strong>{display_label} CPU Critical:</strong> CPU utilisation is at {n['cpu_percent']}% (&gt; 90% threshold)."
            })
        elif n['cpu_percent'] > 75:
            node_attention_items.append({
                'type': 'warning',
                'message': f"<strong>{display_label} CPU Warning:</strong> Elevated CPU utilisation at {n['cpu_percent']}%."
            })

        if n['memory_percent'] > 90:
            node_attention_items.append({
                'type': 'danger',
                'message': f"<strong>{display_label} Memory Critical:</strong> RAM usage is at {n['memory_percent']}% ({n['memory_used_gb']}/{n['memory_total_gb']} GB)."
            })
        elif n['memory_percent'] > 80:
            node_attention_items.append({
                'type': 'warning',
                'message': f"<strong>{display_label} Memory Warning:</strong> RAM usage is at {n['memory_percent']}% ({n['memory_used_gb']}/{n['memory_total_gb']} GB)."
            })

        for d in n['disks']:
            disk_lbl = f"{display_label} — {d['type']}"
            if d['percent'] > 90:
                node_attention_items.append({
                    'type': 'danger',
                    'message': f"<strong>{disk_lbl}:</strong> Critical disk usage at {d['percent']}% ({d['free_gb']} GB free of {d['size_gb']} GB)."
                })
            elif d['percent'] > 80:
                node_attention_items.append({
                    'type': 'warning',
                    'message': f"<strong>{disk_lbl}:</strong> High disk usage at {d['percent']}% ({d['free_gb']} GB free of {d['size_gb']} GB)."
                })

    all_nodes_healthy = all(n['status'] == 'Healthy' for n in active_nodes) if active_nodes else True
    scope_label = "all monitored OLKVM and Proxmox nodes" if selected_node == 'All Nodes' else f"the {selected_node} node scope"
    if all_nodes_healthy:
        default_node_lede_text = (
            f"Infrastructure telemetry across {scope_label} held within the {target_node_sla}% availability SLA target ({kpi_node_uptime} average uptime). "
            f"Compute and storage capacity across {kpi_total_logical_cpus} logical CPUs, {kpi_total_ram_gb} GB RAM, and {kpi_total_storage_gb} GB provisioned storage ({kpi_storage_used_pct}% utilized) remain within normal operational thresholds."
        )
    else:
        default_node_lede_text = (
            f"Infrastructure telemetry for {scope_label} recorded {kpi_nodes_healthy} with an average availability of {kpi_node_uptime}. "
            f"Total provisioned storage stands at {kpi_total_storage_gb} GB ({kpi_used_storage_gb} GB used, {kpi_storage_used_pct}%)."
        )

    return {
        'selected_node': selected_node,
        'prometheus_url': PROMETHEUS_URL,
        'node_overview_data': node_overview_data,
        'filtered_nodes': filtered_nodes,
        'scoped_node_detail': scoped_node_detail,
        'shared_storage_records': shared_storage_records,
        'node_capacity_records': node_capacity_records,
        'kpi_node_uptime': kpi_node_uptime,
        'kpi_nodes_healthy': kpi_nodes_healthy,
        'kpi_total_logical_cpus': kpi_total_logical_cpus,
        'kpi_total_ram_gb': kpi_total_ram_gb,
        'kpi_used_ram_gb': kpi_used_ram_gb,
        'kpi_total_storage_gb': kpi_total_storage_gb,
        'kpi_used_storage_gb': kpi_used_storage_gb,
        'kpi_free_storage_gb': kpi_free_storage_gb,
        'kpi_storage_used_pct': kpi_storage_used_pct,
        'target_node_sla': target_node_sla,
        'node_trend_months': node_trend_months,
        'node_trend_recent_avg': node_trend_recent_avg,
        'node_trend_polyline_points': node_trend_polyline_points,
        'node_trend_area_points': node_trend_area_points,
        'node_sla_y_coord': node_sla_y_coord,
        'node_attention_items': node_attention_items,
        'default_node_lede_text': default_node_lede_text,
        'node_reporting_month_display': target_month_dt.strftime('%B %Y'),
    }


@login_required
def dashboard_node_print(request):
    if not (request.user.is_admin or request.user.is_superuser or request.user.has_perm('api.view_infrastructuredashboard')):
        messages.error(request, 'You do not have permission to view or export the Node Infrastructure report.')
        return redirect('dashboard')

    if request.method == 'POST':
        month_str = request.POST.get('month', '')
        selected_node = request.POST.get('node_select', 'All Nodes')
        summary_narrative = request.POST.get('summary_narrative', '').strip()
    else:
        month_str = request.GET.get('month', '')
        selected_node = request.GET.get('node_select', 'All Nodes')
        summary_narrative = request.GET.get('summary_narrative', '').strip()

    if month_str:
        try:
            reporting_month = datetime.strptime(month_str, '%Y-%m').date()
        except ValueError:
            reporting_month = timezone.now().date().replace(day=1)
    else:
        reporting_month = timezone.now().date().replace(day=1)

    node_ctx = _build_node_dashboard_context(selected_node=selected_node, reporting_month=reporting_month)

    user_display = request.user.get_full_name()
    if not user_display:
        user_display = request.user.username.replace('.', ' ').title()

    lede_text = summary_narrative if summary_narrative else node_ctx['default_node_lede_text']

    ctx = {
        'reporting_month_str': reporting_month.strftime('%B %Y'),
        'extracted_by': user_display,
        'extracted_at': timezone.localtime(timezone.now()).strftime('%d %b %Y, %H:%M'),
        'lede_text': lede_text,
    }
    ctx.update(node_ctx)
    return render(request, 'dashboard_node_print.html', ctx)


@login_required
def dashboard_node_excel(request):
    if not (request.user.is_admin or request.user.is_superuser or request.user.has_perm('api.view_infrastructuredashboard')):
        messages.error(request, 'You do not have permission to export the Node Infrastructure report.')
        return redirect('dashboard')

    month_str = request.GET.get('month', '')
    if month_str:
        try:
            reporting_month = datetime.strptime(month_str, '%Y-%m').date()
        except ValueError:
            reporting_month = timezone.now().date().replace(day=1)
    else:
        reporting_month = timezone.now().date().replace(day=1)

    selected_node = request.GET.get('node_select', 'All Nodes')
    node_ctx = _build_node_dashboard_context(selected_node=selected_node, reporting_month=reporting_month)
    filtered_nodes = node_ctx['filtered_nodes']
    node_capacity_records = node_ctx['node_capacity_records']
    month_display = reporting_month.strftime('%B %Y')

    user_display = request.user.get_full_name()
    if not user_display:
        user_display = request.user.username.replace('.', ' ').title()

    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from openpyxl.utils import get_column_letter

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Node Performance & Capacity"

        font_title = Font(name='Segoe UI', size=14, bold=True, color='002569')
        font_section = Font(name='Segoe UI', size=11, bold=True, color='002569')
        font_header = Font(name='Segoe UI', size=10, bold=True, color='FFFFFF')
        font_body = Font(name='Segoe UI', size=10)

        fill_header = PatternFill(start_color='002569', end_color='002569', fill_type='solid')
        fill_even = PatternFill(start_color='F4F7FC', end_color='F4F7FC', fill_type='solid')

        align_center = Alignment(horizontal='center', vertical='center')
        align_left = Alignment(horizontal='left', vertical='center')

        border_thin = Border(
            left=Side(style='thin', color='D1D7E0'),
            right=Side(style='thin', color='D1D7E0'),
            top=Side(style='thin', color='D1D7E0'),
            bottom=Side(style='thin', color='D1D7E0')
        )

        ws.merge_cells('A1:I1')
        ws['A1'] = f"Node Infrastructure Performance & Capacity Summary ({selected_node}) - {month_display}"
        ws['A1'].font = font_title
        ws['A1'].alignment = align_left
        ws.row_dimensions[1].height = 30

        ws.append([])
        row_num = 3

        headers = [
            "Node", "Hypervisor", "Workloads", "Monthly Avg Uptime (%)", "Local Storage (Used / Total GB)",
            "Storage Util (%)", "Memory (Used / Total GB)", "Memory Util (%)", "Logical CPUs", "CPU Util (%)", "Status"
        ]
        ws.append(headers)
        ws.row_dimensions[row_num].height = 24
        for col_num in range(1, len(headers) + 1):
            c = ws.cell(row=row_num, column=col_num)
            c.font = font_header
            c.fill = fill_header
            c.alignment = align_center
            c.border = border_thin
        row_num += 1

        for n in filtered_nodes:
            node_lbl = "OLKVM" if n['node_name'] == 'OLKVM' else n['display_name']
            r_data = [
                node_lbl,
                n['node_name'],
                n['hosted_systems'],
                f"{n['uptime_percent']}%" if n['has_data'] else "No Data",
                f"{n['local_disk_used_gb']} / {n['local_disk_capacity_gb']}" if n['has_data'] else "No Data",
                f"{n['local_disk_util_pct']}%" if n['has_data'] else "No Data",
                f"{n['memory_used_gb']} / {n['memory_total_gb']}" if n['has_data'] else "No Data",
                f"{n['memory_percent']}%" if n['has_data'] else "No Data",
                n['logical_cpus'],
                f"{n['cpu_percent']}%" if n['has_data'] else "No Data",
                n['status'],
            ]
            ws.append(r_data)
            ws.row_dimensions[row_num].height = 20
            for col_num in range(1, len(headers) + 1):
                c = ws.cell(row=row_num, column=col_num)
                c.font = font_body
                c.border = border_thin
                if row_num % 2 == 0:
                    c.fill = fill_even
                c.alignment = align_left if col_num in (1, 2, 3) else align_center
            row_num += 1

        ws.append([])
        row_num += 1
        ws.cell(row=row_num, column=1, value="Node Capacity Records (3 Disk Tiers & Shared Storage)").font = font_section
        row_num += 1

        cap_headers = [
            "Timestamp", "Node", "Hypervisor", "Storage Tier", "Capacity (GB)", "Used (GB)", "Free (GB)", "Utilisation (%)", "24h Delta (GB)", "24h Delta (%)", "Runway (Days to Full)"
        ]
        ws.append(cap_headers)
        ws.row_dimensions[row_num].height = 24
        for col_num in range(1, len(cap_headers) + 1):
            c = ws.cell(row=row_num, column=col_num)
            c.font = font_header
            c.fill = fill_header
            c.alignment = align_center
            c.border = border_thin
        row_num += 1

        for cap in node_capacity_records:
            cap_node_lbl = "OLKVM" if cap['node_name'] == 'OLKVM' else cap['display_name']
            
            # Format timestamp as '20 Oct 2026 09:15'
            cap_ts = ""
            if cap.get('updated_at') and hasattr(cap['updated_at'], 'strftime'):
                cap_ts = cap['updated_at'].strftime('%d %b %Y %H:%M')
            elif cap.get('date') and hasattr(cap['date'], 'strftime'):
                cap_ts = cap['date'].strftime('%d %b %Y')
            elif hasattr(cap.get('month'), 'strftime'):
                cap_ts = cap['month'].strftime('%d %b %Y')
            else:
                cap_ts = str(cap.get('date') or cap.get('month') or "")

            c_data = [
                cap_ts,
                cap_node_lbl,
                cap['node_name'],
                cap['storage_tier'],
                cap['capacity_gb'],
                cap['used_gb'],
                cap['free_gb'],
                f"{cap['used_percent']}%",
                cap['delta_24h_gb'],
                f"{cap['delta_24h_percent']}%",
                cap['runway_text'],
            ]
            ws.append(c_data)
            ws.row_dimensions[row_num].height = 20
            for col_num in range(1, len(cap_headers) + 1):
                c = ws.cell(row=row_num, column=col_num)
                c.font = font_body
                c.border = border_thin
                if row_num % 2 == 0:
                    c.fill = fill_even
                c.alignment = align_left if col_num in (1, 2, 3, 4) else align_center
            row_num += 1

        ws.append([])
        row_num += 1
        ws.cell(row=row_num, column=1, value=f"Extracted by {user_display} on {timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')}").font = Font(name='Segoe UI', size=9, italic=True)

        for col in ws.columns:
            max_len = 0
            col_val = col[0].column
            col_letter = get_column_letter(col_val) if isinstance(col_val, int) else str(col_val)
            for cell in col:
                if cell.row == 1 or cell.row >= row_num:
                    continue
                if cell.value is not None:
                    max_len = max(max_len, len(str(cell.value)))
            ws.column_dimensions[col_letter].width = max(max_len + 3, 13)

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        safe_scope = selected_node.replace(' ', '_').replace('.', '_')
        filename = f"Node_Performance_Report_{safe_scope}_{reporting_month.strftime('%Y_%m')}.xlsx"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        wb.save(response)
        return response
    except Exception:
        import csv
        response = HttpResponse(content_type='text/csv')
        safe_scope = selected_node.replace(' ', '_').replace('.', '_')
        filename = f"Node_Performance_Report_{safe_scope}_{reporting_month.strftime('%Y_%m')}.csv"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        writer = csv.writer(response)
        writer.writerow([f"Node Infrastructure Performance & Capacity Summary ({selected_node}) - {month_display}"])
        writer.writerow([])
        writer.writerow(["Node", "Hypervisor", "Workloads", "Monthly Avg Uptime (%)", "Local Storage (Used / Total GB)", "Storage Util (%)", "Memory (Used / Total GB)", "Memory Util (%)", "Logical CPUs", "CPU Util (%)", "Status"])
        for n in filtered_nodes:
            node_lbl = "OLKVM" if n['node_name'] == 'OLKVM' else n['display_name']
            writer.writerow([
                node_lbl, n['node_name'], n['hosted_systems'],
                f"{n['uptime_percent']}%", f"{n['local_disk_used_gb']} / {n['local_disk_capacity_gb']}",
                f"{n['local_disk_util_pct']}%", f"{n['memory_used_gb']} / {n['memory_total_gb']}",
                f"{n['memory_percent']}%", n['logical_cpus'], f"{n['cpu_percent']}%", n['status']
            ])

        writer.writerow([])
        writer.writerow(["Node Capacity Records (3 Disk Tiers & Shared Storage)"])
        writer.writerow(["Timestamp", "Node", "Hypervisor", "Storage Tier", "Capacity (GB)", "Used (GB)", "Free (GB)", "Utilisation (%)", "24h Delta (GB)", "24h Delta (%)", "Runway (Days to Full)"])
        for cap in node_capacity_records:
            cap_node_lbl = "OLKVM" if cap['node_name'] == 'OLKVM' else cap['display_name']
            cap_ts = ""
            if cap.get('updated_at') and hasattr(cap['updated_at'], 'strftime'):
                cap_ts = cap['updated_at'].strftime('%d %b %Y %H:%M')
            elif cap.get('date') and hasattr(cap['date'], 'strftime'):
                cap_ts = cap['date'].strftime('%d %b %Y')
            elif hasattr(cap.get('month'), 'strftime'):
                cap_ts = cap['month'].strftime('%d %b %Y')
            else:
                cap_ts = str(cap.get('date') or cap.get('month') or "")
            writer.writerow([
                cap_ts, cap_node_lbl, cap['node_name'], cap['storage_tier'],
                cap['capacity_gb'], cap['used_gb'], cap['free_gb'],
                f"{cap['used_percent']}%", cap['delta_24h_gb'], f"{cap['delta_24h_percent']}%",
                cap['runway_text']
            ])
        return response



@login_required
def server_health_list(request):
    if not (request.user.is_admin or request.user.has_perm('api.view_serverhealth')):
        messages.error(request, 'You do not have permission to view server health records.')
        return redirect('dashboard')

    from django.db.models import Avg, Q

    selected_node = request.GET.get('node_select', 'All Nodes')
    current_db = request.GET.get('database', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')

    node_ctx = _build_node_dashboard_context(selected_node=selected_node)

    queryset = ServerHealth.objects.exclude(entered_by='Script').order_by('-date', '-updated_at')

    if selected_node == 'OLKVM':
        queryset = queryset.filter(
            Q(node_name__iexact='OLKVM') | Q(server__icontains='infra') | Q(server__icontains='ebs')
        )
    elif selected_node == 'Proxmox':
        queryset = queryset.filter(
            Q(node_name__iexact='Proxmox') | Q(server__icontains='pvenode')
        )
    elif selected_node != 'All Nodes':
        queryset = queryset.filter(server__iexact=selected_node)

    if current_db:
        queryset = queryset.filter(database=current_db)
    if start_date:
        queryset = queryset.filter(date__gte=start_date)
    if end_date:
        queryset = queryset.filter(date__lte=end_date)

    paginator = Paginator(queryset, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    kpi_unhealthy_count = queryset.filter(status__in=['Critical', 'Warning']).count()
    avg_cpu = queryset.aggregate(Avg('cpu_percent'))['cpu_percent__avg']
    avg_mem = queryset.aggregate(Avg('memory_percent'))['memory_percent__avg']
    kpi_avg_cpu = f"{round(avg_cpu, 1)}%" if avg_cpu is not None else 'N/A'
    kpi_avg_mem = f"{round(avg_mem, 1)}%" if avg_mem is not None else 'N/A'

    ctx = {
        'page_obj': page_obj,
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'start_date': start_date,
        'end_date': end_date,
        'kpi_unhealthy_count': kpi_unhealthy_count,
        'kpi_avg_cpu': kpi_avg_cpu,
        'kpi_avg_mem': kpi_avg_mem,
    }
    ctx.update(node_ctx)
    return render(request, 'server_health/list.html', ctx)

@login_required
def server_health_create(request):
    if not (request.user.is_admin or request.user.has_perm('api.add_serverhealth')):
        messages.error(request, 'You do not have permission to create server health records.')
        return redirect('server_health_list')

    if request.method == 'POST':
        form = ServerHealthForm(request.POST)
        if form.is_valid():
            health = form.save(commit=False)
            health.created_by = request.user
            health.save()
            messages.success(request, 'Server health record created successfully.')
            return redirect('server_health_list')
    else:
        form = ServerHealthForm()
    return render(request, 'server_health/form.html', {'form': form, 'title': 'Create Server Health Record'})

@login_required
def server_health_edit(request, pk):
    health = get_object_or_404(ServerHealth, pk=pk)
    
    if health.entered_by in ['Script', 'Prometheus']:
        messages.error(request, 'Server health records generated by automated scripts or Prometheus cannot be edited.')
        return redirect('server_health_list')
        
    can_edit_always = request.user.is_admin
    is_creator_within_time = (health.created_by == request.user) and ((timezone.now() - health.created_at).total_seconds() < 300)
    
    if not (can_edit_always or is_creator_within_time):
        messages.error(request, 'You can only edit your own records within 5 minutes of creation.')
        return redirect('server_health_list')
    
    if request.method == 'POST':
        form = ServerHealthForm(request.POST, instance=health)
        if form.is_valid():
            form.save()
            messages.success(request, 'Server health record updated successfully.')
            return redirect('server_health_list')
    else:
        form = ServerHealthForm(instance=health)
    
    return render(request, 'server_health/form.html', {
        'form': form, 
        'title': 'Edit Server Health Record',
        'can_edit': True,
        'health': health
    })

@login_required
def maintenance_list(request):
    if not (request.user.is_admin or request.user.has_perm('api.view_maintenanceregister')):
        messages.error(request, 'You do not have permission to view maintenance records.')
        return redirect('dashboard')
    queryset = MaintenanceRegister.objects.all().order_by('-date', '-created_at')
    
    # Auto-update maintenance statuses (Scheduled -> In Progress -> Completed)
    auto_update_maintenance_statuses()
            
    current_db = request.GET.get('database', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    if current_db:
        queryset = queryset.filter(database=current_db)
    if start_date:
        queryset = queryset.filter(date__gte=start_date)
    if end_date:
        queryset = queryset.filter(date__lte=end_date)
        
    # Calculate KPIs based on the active query filters
    kpis = {
        'scheduled': queryset.filter(status__in=['Scheduled', 'In Progress']).count(),
        'completed': queryset.filter(status='Completed').count(),
        'downtime': round(sum(float(r.duration_hrs) for r in queryset.filter(status__in=['Completed', 'Failed'])), 1),
    }
    
    completed_failed_count = queryset.filter(status__in=['Completed', 'Failed']).count()
    if completed_failed_count > 0:
        kpis['success_rate'] = round((kpis['completed'] / completed_failed_count * 100.0), 1)
    else:
        kpis['success_rate'] = 100.0
        
    paginator = Paginator(queryset, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'maintenance/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'start_date': start_date,
        'end_date': end_date,
        'kpis': kpis
    })

@login_required
def maintenance_create(request):
    if not (request.user.is_admin or request.user.has_perm('api.add_maintenanceregister')):
        messages.error(request, 'You do not have permission to create maintenance records.')
        return redirect('maintenance_list')

    if request.method == 'POST':
        form = MaintenanceRegisterForm(request.POST)
        if form.is_valid():
            maintenance = form.save(commit=False)
            maintenance.created_by = request.user
            maintenance.save()
            messages.success(request, 'Maintenance record created successfully.')
            return redirect('maintenance_list')
    else:
        form = MaintenanceRegisterForm()
    return render(request, 'maintenance/form.html', {'form': form, 'title': 'Create Maintenance Record'})

@login_required
def maintenance_edit(request, pk):
    maintenance = get_object_or_404(MaintenanceRegister, pk=pk)
    
    can_edit_always = request.user.is_admin
    is_creator_within_time = (maintenance.created_by == request.user) and ((timezone.now() - maintenance.created_at).total_seconds() < 300)
    
    if not (can_edit_always or is_creator_within_time):
        messages.error(request, 'You can only edit your own records within 5 minutes of creation.')
        return redirect('maintenance_list')
    
    if request.method == 'POST':
        form = MaintenanceRegisterForm(request.POST, instance=maintenance)
        if form.is_valid():
            form.save()
            messages.success(request, 'Maintenance record updated successfully.')
            return redirect('maintenance_list')
    else:
        form = MaintenanceRegisterForm(instance=maintenance)
    
    return render(request, 'maintenance/form.html', {
        'form': form, 
        'title': 'Edit Maintenance Record',
        'can_edit': True,
        'maintenance': maintenance
    })

@login_required
def capacity_list(request):
    if not (request.user.is_admin or request.user.has_perm('api.view_capacityplanning')):
        messages.error(request, 'You do not have permission to view capacity planning records.')
        return redirect('dashboard')
    queryset = CapacityPlanning.objects.all().order_by('-month', '-updated_at')
    
    current_db = request.GET.get('database', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    if current_db:
        queryset = queryset.filter(database=current_db)
    if start_date:
        queryset = queryset.filter(month__gte=start_date)
    if end_date:
        queryset = queryset.filter(month__lte=end_date)
        
    paginator = Paginator(queryset, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Capacity Redesign calculations
    dbs = ['Oracle', 'MySQL', 'MS SQL Server']
    latest_records = {}
    for db in dbs:
        rec = CapacityPlanning.objects.filter(database=db).order_by('-month', '-updated_at').first()
        if rec:
            latest_records[db] = rec
            
    # Calculate overall KPIs
    kpi_capacity = 0.0
    kpi_used = 0.0
    kpi_free = 0.0
    kpi_growth = 0.0
    kpi_size = 0.0
    
    for db, rec in latest_records.items():
        if current_db and db != current_db:
            continue
        cap = float(rec.disk_capacity_gb)
        used_pct = float(rec.disk_used_percent)
        free = float(rec.available_capacity_gb)
        growth = float(rec.growth_gb)
        size = float(rec.database_size_gb)
        
        # Handle MySQL July 0.00 capacity inconsistency in-memory
        if cap == 0.0 and free == 0.0 and used_pct > 0:
            cap = size * 5.0 if used_pct == 20 else size / (used_pct/100.0)
            free = cap - size
            
        kpi_capacity += cap
        kpi_used += (cap * used_pct / 100.0)
        kpi_free += free
        kpi_growth += growth
        kpi_size += size
        
    kpi_used_percent = round((kpi_used / kpi_capacity * 100.0), 1) if kpi_capacity > 0 else 0.0
    kpi_growth_percent = round((kpi_growth / kpi_capacity * 100.0), 2) if kpi_capacity > 0 else 0.0
    
    # Build capacity health panels
    health_panels = []
    for db in dbs:
        rec = latest_records.get(db)
        if rec:
            cap = float(rec.disk_capacity_gb)
            used_pct = float(rec.disk_used_percent)
            free = float(rec.available_capacity_gb)
            growth = float(rec.growth_gb)
            size = float(rec.database_size_gb)
            
            if cap == 0.0 and free == 0.0 and used_pct > 0:
                cap = size * 5.0 if used_pct == 20 else size / (used_pct/100.0)
                free = cap - size
                
            if used_pct < 70.0:
                status = 'Healthy'
                status_class = 'success'
            elif used_pct < 85.0:
                status = 'Monitor'
                status_class = 'warning'
            elif used_pct < 95.0:
                status = 'Warning'
                status_class = 'orange'
            else:
                status = 'Critical'
                status_class = 'danger'
                
            health_panels.append({
                'database': db,
                'used_percent': round(used_pct, 1),
                'used_gb': round(cap * used_pct / 100.0, 1),
                'capacity_gb': round(cap, 1),
                'database_size_gb': round(size, 1),
                'free_gb': round(free, 1),
                'growth_gb': round(growth, 2),
                'growth_percent': round((growth / size * 100.0), 2) if size > 0 else 0.0,
                'status': status,
                'status_class': status_class
            })
            
    # Build growth trend chart data (SVG coordinates)
    trend_db = current_db if current_db else 'Oracle'
    trend_recs = CapacityPlanning.objects.filter(database=trend_db).order_by('month')
    trend_months_list = list(trend_recs)[-6:]
    
    trend_polyline_points = ""
    trend_data_points = []
    if trend_months_list:
        max_gb = float(max(r.database_size_gb for r in trend_months_list))
        min_gb = float(min(r.database_size_gb for r in trend_months_list))
        gb_range = max(1.0, max_gb - min_gb)
        
        svg_points = []
        for idx, r in enumerate(trend_months_list):
            m_val = float(r.database_size_gb)
            x = 40 + idx * 64
            y = 20 + (max_gb - m_val) * 80 / gb_range
            svg_points.append(f"{x},{y}")
            
            trend_data_points.append({
                'x': x,
                'y': y,
                'text_y': y - 8,
                'name': r.month.strftime('%b'),
                'value': round(m_val, 1)
            })
        trend_polyline_points = " ".join(svg_points)
        
    # Build Forecast Block (Linear projection based on last 3 months)
    forecast_info = {}
    if current_db:
        # Specific database scope
        db_recs = CapacityPlanning.objects.filter(database=current_db).order_by('-month', '-updated_at')[:3]
        if db_recs.exists():
            latest = db_recs[0]
            avg_growth = sum(float(r.growth_gb) for r in db_recs) / len(db_recs)
            
            cap = float(latest.disk_capacity_gb)
            used_pct = float(latest.disk_used_percent)
            free = float(latest.available_capacity_gb)
            size = float(latest.database_size_gb)
            
            if cap == 0.0 and free == 0.0 and used_pct > 0:
                cap = size * 5.0 if used_pct == 20 else size / (used_pct/100.0)
                free = cap - size
                
            warning_used_gb = cap * 0.8
            current_used_gb = cap * used_pct / 100.0
            space_left_gb = warning_used_gb - current_used_gb
            
            if avg_growth > 0:
                headroom_months = space_left_gb / avg_growth
                daily_burn = avg_growth / 30.4
                runway_days = int(free / daily_burn) if daily_burn > 0 else 9999
                if headroom_months < 0:
                    headroom_str = "Exceeded threshold"
                    est_date_str = "Exceeded"
                elif headroom_months > 60:
                    headroom_str = "> 5 years"
                    est_date_str = "Safe"
                else:
                    headroom_str = f"{round(headroom_months, 1)} months"
                    est_days = headroom_months * 30.44
                    est_date = latest.month + timedelta(days=est_days)
                    est_date_str = est_date.strftime('%B %Y')
            else:
                headroom_str = "Infinite"
                est_date_str = "Safe"
                runway_days = 9999

            if runway_days <= 30:
                runway_text = f"{runway_days} days"
                runway_badge = 'critical'
            elif runway_days <= 90:
                runway_text = f"{runway_days} days"
                runway_badge = 'warning'
            elif runway_days <= 365:
                mos = round(runway_days / 30.4, 1)
                runway_text = f"{mos} mos ({runway_days}d)"
                runway_badge = 'healthy'
            else:
                runway_text = "> 1 Year"
                runway_badge = 'stable'
                
            forecast_info = {
                'database': current_db,
                'current_used_pct': round(used_pct, 1),
                'avg_growth': round(avg_growth, 2),
                'headroom_months': headroom_str,
                'est_date': est_date_str,
                'runway_days': runway_days,
                'runway_text': runway_text,
                'runway_badge': runway_badge,
            }
    else:
        # Fleet-wide scope (All Databases combined)
        unique_months = CapacityPlanning.objects.order_by('-month').values_list('month', flat=True).distinct()[:3]
        if unique_months:
            fleet_months = []
            for month in unique_months:
                month_recs = CapacityPlanning.objects.filter(month=month)
                total_cap = 0.0
                total_used = 0.0
                total_growth = 0.0
                
                for r in month_recs:
                    cap = float(r.disk_capacity_gb)
                    used_pct = float(r.disk_used_percent)
                    size = float(r.database_size_gb)
                    free = float(r.available_capacity_gb)
                    
                    if cap == 0.0 and free == 0.0 and used_pct > 0:
                        cap = size * 5.0 if used_pct == 20 else size / (used_pct/100.0)
                        
                    total_cap += cap
                    total_used += (cap * used_pct / 100.0)
                    total_growth += float(r.growth_gb)
                    
                fleet_months.append({
                    'month': month,
                    'capacity_gb': total_cap,
                    'used_gb': total_used,
                    'growth_gb': total_growth
                })
                
            if fleet_months:
                latest = fleet_months[0]
                avg_growth = sum(m['growth_gb'] for m in fleet_months) / len(fleet_months)
                
                cap = latest['capacity_gb']
                used_pct = (latest['used_gb'] / cap * 100.0) if cap > 0 else 0.0
                
                warning_used_gb = cap * 0.8
                current_used_gb = latest['used_gb']
                space_left_gb = warning_used_gb - current_used_gb
                
                if avg_growth > 0:
                    headroom_months = space_left_gb / avg_growth
                    fleet_free = cap - latest['used_gb']
                    daily_burn = avg_growth / 30.4
                    runway_days = int(fleet_free / daily_burn) if daily_burn > 0 else 9999
                    if headroom_months < 0:
                        headroom_str = "Exceeded threshold"
                        est_date_str = "Exceeded"
                    elif headroom_months > 60:
                        headroom_str = "> 5 years"
                        est_date_str = "Safe"
                    else:
                        headroom_str = f"{round(headroom_months, 1)} months"
                        est_days = headroom_months * 30.44
                        est_date = latest['month'] + timedelta(days=est_days)
                        est_date_str = est_date.strftime('%B %Y')
                else:
                    headroom_str = "Infinite"
                    est_date_str = "Safe"
                    runway_days = 9999

                if runway_days <= 30:
                    runway_text = f"{runway_days} days"
                    runway_badge = 'critical'
                elif runway_days <= 90:
                    runway_text = f"{runway_days} days"
                    runway_badge = 'warning'
                elif runway_days <= 365:
                    mos = round(runway_days / 30.4, 1)
                    runway_text = f"{mos} mos ({runway_days}d)"
                    runway_badge = 'healthy'
                else:
                    runway_text = "> 1 Year"
                    runway_badge = 'stable'
                    
                forecast_info = {
                    'database': 'All Databases',
                    'current_used_pct': round(used_pct, 1),
                    'avg_growth': round(avg_growth, 2),
                    'headroom_months': headroom_str,
                    'est_date': est_date_str,
                    'runway_days': runway_days,
                    'runway_text': runway_text,
                    'runway_badge': runway_badge,
                }
    
    return render(request, 'capacity/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'start_date': start_date,
        'end_date': end_date,
        
        # Redesign KPIs
        'kpi_capacity': round(kpi_capacity, 1),
        'kpi_used_gb': round(kpi_used, 1),
        'kpi_used_percent': kpi_used_percent,
        'kpi_free_gb': round(kpi_free, 1),
        'kpi_growth_gb': round(kpi_growth, 2),
        'kpi_growth_percent': kpi_growth_percent,
        'health_panels': health_panels,
        'trend_db': trend_db,
        'trend_polyline_points': trend_polyline_points,
        'trend_data_points': trend_data_points,
        'forecast_info': forecast_info
    })

@login_required
def capacity_print(request):
    if not (request.user.is_admin or request.user.has_perm('api.view_capacityplanning')):
        messages.error(request, 'You do not have permission to print capacity planning records.')
        return redirect('capacity_list')
    queryset = CapacityPlanning.objects.all().order_by('-month', '-updated_at')
    
    current_db = request.GET.get('database', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    if current_db:
        queryset = queryset.filter(database=current_db)
    if start_date:
        queryset = queryset.filter(month__gte=start_date)
    if end_date:
        queryset = queryset.filter(month__lte=end_date)

    # Format user extracted name
    user = request.user
    full_name = f"{user.first_name} {user.last_name}".strip()
    if not full_name:
        username = user.username
        if '.' in username:
            parts = username.split('.')
            full_name = " ".join([p.capitalize() for p in parts])
        else:
            full_name = username.capitalize()

    context = {
        'queryset': queryset,
        'current_db': current_db if current_db else 'All Databases',
        'start_date': start_date,
        'end_date': end_date,
        'extracted_by': full_name,
        'extracted_at': timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M:%S'),
        'reporting_period_str': f"{start_date} to {end_date}" if (start_date or end_date) else "All Time"
    }
    return render(request, 'capacity/print.html', context)

@login_required
def capacity_excel(request):
    if not (request.user.is_admin or request.user.has_perm('api.view_capacityplanning')):
        messages.error(request, 'You do not have permission to export capacity planning records.')
        return redirect('capacity_list')
    
    queryset = CapacityPlanning.objects.all().order_by('-month', '-updated_at')
    current_db = request.GET.get('database', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    if current_db:
        queryset = queryset.filter(database=current_db)
    if start_date:
        queryset = queryset.filter(month__gte=start_date)
    if end_date:
        queryset = queryset.filter(month__lte=end_date)

    db_display = current_db if current_db else "All Databases"
    period_display = f"{start_date}_to_{end_date}" if (start_date or end_date) else "All_Time"
    
    # Format user name
    user = request.user
    full_name = f"{user.first_name} {user.last_name}".strip()
    if not full_name:
        username = user.username
        if '.' in username:
            parts = username.split('.')
            full_name = " ".join([p.capitalize() for p in parts])
        else:
            full_name = username.capitalize()

    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from openpyxl.utils import get_column_letter
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Capacity Planning"
        
        # Style Definitions
        font_title = Font(name='Segoe UI', size=14, bold=True, color='002569')
        font_header = Font(name='Segoe UI', size=11, bold=True, color='FFFFFF')
        font_body = Font(name='Segoe UI', size=10)
        
        fill_header = PatternFill(start_color='002569', end_color='002569', fill_type='solid')
        fill_even = PatternFill(start_color='F4F7FC', end_color='F4F7FC', fill_type='solid')
        
        align_center = Alignment(horizontal='center', vertical='center')
        align_left = Alignment(horizontal='left', vertical='center')
        
        border_thin = Border(
            left=Side(style='thin', color='D1D7E0'),
            right=Side(style='thin', color='D1D7E0'),
            top=Side(style='thin', color='D1D7E0'),
            bottom=Side(style='thin', color='D1D7E0')
        )
        
        # Title Block
        ws.merge_cells('A1:J1')
        ws['A1'] = f"Capacity Tracking Report for {db_display} - ({period_display.replace('_', ' ')})"
        ws['A1'].font = font_title
        ws['A1'].alignment = align_left
        ws.row_dimensions[1].height = 30
        
        ws.append([]) # Empty row
        
        # Headers
        headers = [
            "Month", "Database", "Database Size (GB)", "Growth (GB)", "Growth (%)",
            "Total Disk Capacity (GB)", "Disk Used %", "Tablespace Used %", "Free Disk Space (GB)", "Largest Table"
        ]
        ws.append(headers)
        ws.row_dimensions[3].height = 25
        
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=3, column=col_num)
            cell.font = font_header
            cell.fill = fill_header
            cell.alignment = align_center
            cell.border = border_thin
            
        row_num = 4
        for item in queryset:
            growth_pct = f"{item.growth_percent}%"
            disk_cap = item.disk_capacity_gb
            tablespace_val = f"{item.tablespace_used_percent}%" if item.database != 'MS SQL Server' else 'N/A'
            
            row_data = [
                item.month.strftime('%B %Y'),
                item.database,
                float(item.database_size_gb),
                float(item.growth_gb),
                growth_pct,
                float(disk_cap),
                float(item.disk_used_percent),
                tablespace_val if tablespace_val == 'N/A' else float(item.tablespace_used_percent),
                float(item.available_capacity_gb),
                item.largest_table
            ]
            ws.append(row_data)
            ws.row_dimensions[row_num].height = 20
            
            for col_num in range(1, 11):
                cell = ws.cell(row=row_num, column=col_num)
                cell.font = font_body
                cell.border = border_thin
                if row_num % 2 == 0:
                    cell.fill = fill_even
                
                if col_num in [1, 2, 10]:
                    cell.alignment = align_left
                else:
                    cell.alignment = align_center
            row_num += 1
            
        # Add metadata footer
        ws.append([])
        row_num += 1
        ws.cell(row=row_num, column=1, value=f"Extracted by {full_name} on {timezone.now().strftime('%d/%m/%Y')}").font = Font(name='Segoe UI', size=9, italic=True)
        
        # Adjust Column Widths
        for col in ws.columns:
            max_len = 0
            col_val = col[0].column
            col_letter = get_column_letter(col_val) if isinstance(col_val, int) else str(col_val)
            for cell in col:
                if cell.row == 1 or cell.row > row_num:
                    continue
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)
            
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        filename = f"Capacity_Report_{db_display.replace(' ', '_')}_{period_display}.xlsx"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        wb.save(response)
        return response
        
    except Exception:
        import csv
        response = HttpResponse(content_type='text/csv')
        filename = f"Capacity_Report_{db_display.replace(' ', '_')}_{period_display}.csv"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        writer = csv.writer(response)
        writer.writerow([f"Capacity Tracking Report for {db_display} - ({period_display.replace('_', ' ')})"])
        writer.writerow([])
        writer.writerow([
            "Month", "Database", "Database Size (GB)", "Growth (GB)", "Growth (%)",
            "Total Disk Capacity (GB)", "Disk Used %", "Tablespace Used %", "Free Disk Space (GB)", "Largest Table"
        ])
        for item in queryset:
            tablespace_val = f"{item.tablespace_used_percent}%" if item.database != 'MS SQL Server' else 'N/A'
            writer.writerow([
                item.month.strftime('%B %Y'),
                item.database,
                item.database_size_gb,
                item.growth_gb,
                f"{item.growth_percent}%",
                item.disk_capacity_gb,
                item.disk_used_percent,
                tablespace_val,
                item.available_capacity_gb,
                item.largest_table
            ])
            
        writer.writerow([])
        writer.writerow([f"Extracted by {full_name} on {timezone.now().strftime('%d/%m/%Y')}"])
        return response

@login_required
def capacity_create(request):
    if not (request.user.is_admin or request.user.has_perm('api.add_capacityplanning')):
        messages.error(request, 'You do not have permission to create capacity planning records.')
        return redirect('capacity_list')

    if request.method == 'POST':
        form = CapacityPlanningForm(request.POST)
        if form.is_valid():
            capacity = form.save(commit=False)
            # Check if a record already exists for the same database and month
            existing_record = CapacityPlanning.objects.filter(
                database=capacity.database,
                month__year=capacity.month.year,
                month__month=capacity.month.month
            ).first()
            if existing_record:
                # Update existing record
                existing_record.database_size_gb = capacity.database_size_gb
                existing_record.available_capacity_gb = capacity.available_capacity_gb
                existing_record.disk_used_percent = capacity.disk_used_percent
                existing_record.tablespace_used_percent = capacity.tablespace_used_percent
                existing_record.largest_table = capacity.largest_table
                existing_record.growth_gb = capacity.growth_gb
                existing_record.created_by = request.user
                existing_record.save()
                messages.success(request, f"Updated existing capacity planning record for {capacity.database} in {capacity.month.strftime('%B %Y')}.")
            else:
                capacity.created_by = request.user
                capacity.save()
                messages.success(request, 'Capacity planning record created successfully.')
            return redirect('capacity_list')
    else:
        form = CapacityPlanningForm()
    return render(request, 'capacity/form.html', {'form': form, 'title': 'Create Capacity Planning Record'})

@login_required
def capacity_edit(request, pk):
    capacity = get_object_or_404(CapacityPlanning, pk=pk)
    
    can_edit_always = request.user.is_admin
    is_creator_within_time = (capacity.created_by == request.user) and ((timezone.now() - capacity.created_at).total_seconds() < 300)
    
    if not (can_edit_always or is_creator_within_time):
        messages.error(request, 'You can only edit your own records within 5 minutes of creation.')
        return redirect('capacity_list')
    
    if request.method == 'POST':
        form = CapacityPlanningForm(request.POST, instance=capacity)
        if form.is_valid():
            form.save()
            messages.success(request, 'Capacity planning record updated successfully.')
            return redirect('capacity_list')
    else:
        form = CapacityPlanningForm(instance=capacity)
    
    return render(request, 'capacity/form.html', {
        'form': form, 
        'title': 'Edit Capacity Planning Record',
        'can_edit': True,
        'capacity': capacity
    })

# --- DELETE VIEWS ---

@login_required
def incident_delete(request, pk):
    if request.method != 'POST':
        return redirect('incident_list')
    obj = get_object_or_404(IncidentRegister, pk=pk)
    
    can_delete_always = request.user.is_admin
    is_creator_within_time = (obj.created_by == request.user) and ((timezone.now() - obj.created_at).total_seconds() < 300)
    
    if not (can_delete_always or is_creator_within_time):
        messages.error(request, 'You can only delete your own records within 5 minutes of creation.')
        return redirect(request.META.get('HTTP_REFERER', 'incident_list'))
            
    obj.delete()
    messages.success(request, 'Incident record deleted successfully.')
    return redirect(request.META.get('HTTP_REFERER', 'incident_list'))

@login_required
def replication_delete(request, pk):
    if request.method != 'POST':
        return redirect('replication_list')
    obj = get_object_or_404(ReplicationMonitor, pk=pk)
    
    can_delete_always = request.user.is_admin
    is_creator_within_time = (obj.created_by == request.user) and ((timezone.now() - obj.created_at).total_seconds() < 300)
    
    if not (can_delete_always or is_creator_within_time):
        messages.error(request, 'You can only delete your own records within 5 minutes of creation.')
        return redirect(request.META.get('HTTP_REFERER', 'replication_list'))
            
    obj.delete()
    messages.success(request, 'Replication record deleted successfully.')
    return redirect(request.META.get('HTTP_REFERER', 'replication_list'))

@login_required
def backup_delete(request, pk):
    if request.method != 'POST':
        return redirect('backup_list')
    obj = get_object_or_404(BackupRecovery, pk=pk)
    
    can_delete_always = request.user.is_admin
    is_creator_within_time = (obj.created_by == request.user) and ((timezone.now() - obj.created_at).total_seconds() < 300)
    
    if not (can_delete_always or is_creator_within_time):
        messages.error(request, 'You can only delete your own records within 5 minutes of creation.')
        return redirect(request.META.get('HTTP_REFERER', 'backup_list'))
            
    obj.delete()
    messages.success(request, 'Backup record deleted successfully.')
    return redirect(request.META.get('HTTP_REFERER', 'backup_list'))

@login_required
def server_health_delete(request, pk):
    if request.method != 'POST':
        return redirect('server_health_list')
    obj = get_object_or_404(ServerHealth, pk=pk)
    
    if obj.entered_by == 'Script' and not request.user.is_admin:
        messages.error(request, 'Server health records generated by automated scripts cannot be deleted.')
        return redirect(request.META.get('HTTP_REFERER', 'server_health_list'))
        
    can_delete_always = request.user.is_admin
    is_creator_within_time = (obj.created_by == request.user) and ((timezone.now() - obj.created_at).total_seconds() < 300)
    
    if not (can_delete_always or is_creator_within_time):
        messages.error(request, 'You can only delete your own records within 5 minutes of creation.')
        return redirect(request.META.get('HTTP_REFERER', 'server_health_list'))
            
    obj.delete()
    messages.success(request, 'Server health record deleted successfully.')
    return redirect(request.META.get('HTTP_REFERER', 'server_health_list'))

@login_required
def maintenance_delete(request, pk):
    if request.method != 'POST':
        return redirect('maintenance_list')
    obj = get_object_or_404(MaintenanceRegister, pk=pk)
    
    can_delete_always = request.user.is_admin
    is_creator_within_time = (obj.created_by == request.user) and ((timezone.now() - obj.created_at).total_seconds() < 300)
    
    if not (can_delete_always or is_creator_within_time):
        messages.error(request, 'You can only delete your own records within 5 minutes of creation.')
        return redirect(request.META.get('HTTP_REFERER', 'maintenance_list'))
            
    obj.delete()
    messages.success(request, 'Maintenance record deleted successfully.')
    return redirect(request.META.get('HTTP_REFERER', 'maintenance_list'))

@login_required
def capacity_delete(request, pk):
    if request.method != 'POST':
        return redirect('capacity_list')
    obj = get_object_or_404(CapacityPlanning, pk=pk)
    
    can_delete_always = request.user.is_admin
    is_creator_within_time = (obj.created_by == request.user) and ((timezone.now() - obj.created_at).total_seconds() < 300)
    
    if not (can_delete_always or is_creator_within_time):
        messages.error(request, 'You can only delete your own records within 5 minutes of creation.')
        return redirect(request.META.get('HTTP_REFERER', 'capacity_list'))
            
    obj.delete()
    messages.success(request, 'Capacity planning record deleted successfully.')
    return redirect(request.META.get('HTTP_REFERER', 'capacity_list'))


@csrf_exempt
@require_POST
def replication_ingest(request):
    api_key = request.headers.get('X-API-KEY')
    expected_key = os.environ.get('REPLICATION_API_KEY', 'uptimetracker_api_9f2c1d8e45x9')
    
    if api_key != expected_key:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
        
    try:
        data = json.loads(request.body)
        
        # Determine severity based on threads and delay
        severity = 'Low'
        if data.get('io_thread') != 'Yes' or data.get('sql_thread') != 'Yes':
            severity = 'Critical'
        elif data.get('seconds_behind', 0) > 300:
            severity = 'High'
        elif data.get('seconds_behind', 0) > 60:
            severity = 'Medium'
            
        replication = ReplicationMonitor.objects.create(
            date=timezone.now().date(),
            master=data.get('master', 'Unknown'),
            replica=data.get('replica', 'Unknown'),
            io_thread=data.get('io_thread', 'No'),
            sql_thread=data.get('sql_thread', 'No'),
            seconds_behind=data.get('seconds_behind', 0),
            last_error=data.get('last_error', ''),
            database='MySQL',
            severity=severity
        )
        return JsonResponse({'status': 'success', 'id': replication.id})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)

@csrf_exempt
@require_POST
def backup_ingest(request):
    api_key = request.headers.get('X-API-KEY')
    expected_key = os.environ.get('REPLICATION_API_KEY', 'uptimetracker_api_9f2c1d8e45x9')  # We can reuse the same API key
    
    if api_key != expected_key:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
        
    try:
        data = json.loads(request.body)
        
        start_str = data.get('start')
        start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00')) if start_str else datetime.now()
        
        # De-duplication check: avoid double registering the same backup
        exists = BackupRecovery.objects.filter(
            database=data.get('database', 'MySQL'),
            database_instance=data.get('database_instance', 'Unknown'),
            backup_type=data.get('backup_type', 'Incremental'),
            start=start_dt
        ).exists()
        
        if exists:
            return JsonResponse({'status': 'ignored', 'message': 'Backup already registered'}, status=200)
            
        backup_used = data.get('backup_used', 'N/A')
        location = data.get('location', 'Unknown')
        compression = data.get('compression', True)
        b_type = data.get('backup_type', 'Incremental')
        db_type = data.get('database', 'MySQL')
        db_inst = data.get('database_instance', 'Unknown')
        
        # Guard against log checkpoint strings mistakenly sent as file references
        if any(k in str(backup_used).lower() for k in ['binlog', 'checkpoint', 'nfs']) or backup_used in ('Unknown', 'N/A', ''):
            if db_type == 'MySQL' or 'nickle' in str(db_inst).lower():
                inst_name = 'nickledbnew' if db_inst == 'Unknown' else db_inst
                backup_used = f"{inst_name}_{start_dt.strftime('%Y-%m-%d_%H-%M-%S')}_{b_type.lower()}.tar.gz"
                location = backup_used
                compression = True

        backup = BackupRecovery.objects.create(
            date=start_dt.date(),
            record_type='Backup',
            database=db_type,
            database_instance=db_inst,
            backup_type=b_type,
            backup_used=backup_used,
            start=start_dt,
            end=data.get('end'),
            duration_mins=data.get('duration_mins', 0),
            outcome=data.get('outcome', 'Failed'),
            failure_reason=data.get('failure_reason', ''),
            location=location,
            verified=data.get('verified', False),
            compression=compression,
        )
        return JsonResponse({'status': 'success', 'id': backup.id})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)

@csrf_exempt
@require_POST
def server_health_ingest(request):
    api_key = request.headers.get('X-API-KEY')
    expected_key = os.environ.get('REPLICATION_API_KEY', 'uptimetracker_api_9f2c1d8e45x9')
    
    if api_key != expected_key:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
        
    try:
        data = json.loads(request.body)
        
        cpu = float(data.get('cpu_percent', 0))
        memory = float(data.get('memory_percent', 0))
        d1_pct = float(data.get('disk_1_percent', 0))
        d2_pct = float(data.get('disk_2_percent', 0))
        d3_pct = float(data.get('disk_3_percent', 0))
        disk = max(float(data.get('disk_percent', 0)), d1_pct, d2_pct, d3_pct)
        uptime_pct = float(data.get('uptime_percent', 100.0))
        
        status = 'Healthy'
        if cpu > 90 or memory > 90 or disk > 90 or uptime_pct < 99.0:
            status = 'Critical'
        elif cpu > 75 or memory > 80 or disk > 80 or uptime_pct < 99.9:
            status = 'Warning'
            
        # Check for optional custom date override (for backfilling)
        date_str = data.get('date')
        if date_str:
            health_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        else:
            health_date = datetime.now().date()

        node_name = data.get('node_name', '')
        server_name = data.get('server', 'Unknown')
        db_name = data.get('database', 'MySQL')
        if not node_name:
            if db_name == 'Oracle' or any(k in server_name.lower() for k in ['olkvm', 'kvm', 'nmsinfra', 'nmsebs']):
                node_name = 'OLKVM'
            else:
                node_name = 'Proxmox'

        source = data.get('entered_by', 'Prometheus' if 'disk_1_type' in data or 'node_name' in data else 'Script')

        # Disable external script logging; only Prometheus (and manual UI entries) manage health metrics
        if source == 'Script' or data.get('entered_by') == 'Script':
            return JsonResponse({
                'status': 'disabled',
                'message': 'Direct script logging for server health is disabled. Health metrics are ingested exclusively via Prometheus.'
            }, status=200)

        if server_name.startswith('nmsinfra'):
            server_name = server_name.replace('nmsinfra', 'infra', 1)
        elif server_name.startswith('nmsebs'):
            server_name = server_name.replace('nmsebs', 'ebs', 1)

        raw_hosted = data.get('hosted_systems', '')
        import re
        clean_hosted = re.sub(r'\s*-\s*\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', '', raw_hosted)
        clean_hosted = re.sub(r'\s*\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '', clean_hosted)
        clean_hosted = clean_hosted.replace('nmsinfra', 'infra').replace('nmsebs', 'ebs')
        clean_hosted = re.sub(r'\(\s*\)', '', clean_hosted).strip(' -()')

        # Find existing entry for this server/node/date combination
        health = ServerHealth.objects.filter(
            date=health_date,
            server=server_name,
            database=db_name
        ).first()

        fields_map = {
            'node_name': node_name,
            'hosted_systems': clean_hosted,
            'entered_by': source,
            'uptime_percent': uptime_pct,
            'uptime_days': float(data.get('uptime_days', 0)),
            'logical_cpus': int(data.get('logical_cpus', data.get('threads', 0))),
            'cpu_percent': cpu,
            'memory_percent': memory,
            'memory_total_gb': float(data.get('memory_total_gb', 0)),
            'memory_used_gb': float(data.get('memory_used_gb', 0)),
            'disk_percent': disk,
            'storage_growth_gb': float(data.get('storage_growth_gb', 0)),
            'storage_growth_pct': float(data.get('storage_growth_pct', 0)),
            'disk_1_type': data.get('disk_1_type', 'Disk 1 (SSD / OS)'),
            'disk_1_size_gb': float(data.get('disk_1_size_gb', 0)),
            'disk_1_used_gb': float(data.get('disk_1_used_gb', 0)),
            'disk_1_percent': d1_pct if d1_pct > 0 else disk,
            'disk_2_type': data.get('disk_2_type', 'Disk 2 (SAS / Data)'),
            'disk_2_size_gb': float(data.get('disk_2_size_gb', 0)),
            'disk_2_used_gb': float(data.get('disk_2_used_gb', 0)),
            'disk_2_percent': d2_pct,
            'disk_3_type': data.get('disk_3_type', 'Disk 3 (SAN / Backup)'),
            'disk_3_size_gb': float(data.get('disk_3_size_gb', 0)),
            'disk_3_used_gb': float(data.get('disk_3_used_gb', 0)),
            'disk_3_percent': d3_pct,
            'tablespace_percent': float(data.get('tablespace_percent', disk)),
            'connections': int(data.get('connections', 0)),
            'slow_queries': int(data.get('slow_queries', 0)),
            'threads': int(data.get('threads', 0)),
            'status': status,
        }

        if health:
            for k, v in fields_map.items():
                setattr(health, k, v)
            health.save()
        else:
            health = ServerHealth.objects.create(
                date=health_date,
                server=server_name,
                database=db_name,
                **fields_map
            )
        return JsonResponse({'status': 'success', 'id': health.id, 'node_name': health.node_name})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)
 
@csrf_exempt
@require_POST
def capacity_ingest(request):
    api_key = request.headers.get('X-API-KEY')
    expected_key = os.environ.get('REPLICATION_API_KEY', 'uptimetracker_api_9f2c1d8e45x9')
    
    if api_key != expected_key:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
        
    try:
        data = json.loads(request.body)
        
        database_instance = data.get('database', 'MySQL')
        incoming_size = Decimal(str(data.get('database_size_gb', 0)))
        
        # Get client IP address for debugging
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        client_ip = x_forwarded_for.split(',')[0] if x_forwarded_for else request.META.get('REMOTE_ADDR')
        print(f"[DEBUG CAPACITY] Received payload from IP {client_ip} for DB '{database_instance}': size_gb={incoming_size}, largest_table='{data.get('largest_table')}'")
        
        # Check if custom month is provided in payload (e.g. "2026-07")
        month_str = data.get('month')
        if month_str:
            try:
                if len(month_str) == 7:
                    first_day_of_month = datetime.strptime(month_str, "%Y-%m").date()
                else:
                    first_day_of_month = datetime.strptime(month_str[:10], "%Y-%m-%d").date().replace(day=1)
            except ValueError:
                first_day_of_month = datetime.now().date().replace(day=1)
        else:
            first_day_of_month = datetime.now().date().replace(day=1)
        
        # Calculate growth by comparing with the size from the most recent PREVIOUS month
        most_recent = CapacityPlanning.objects.filter(
            database=database_instance,
            month__lt=first_day_of_month
        ).order_by('-month').first()
        
        growth = Decimal('0.00')
        if most_recent:
            growth = incoming_size - most_recent.database_size_gb
            
        # Find the existing entry for this database/month combination
        capacity = CapacityPlanning.objects.filter(
            month=first_day_of_month,
            database=database_instance
        ).first()
        
        if capacity:
            capacity.database_size_gb = incoming_size
            capacity.disk_used_percent = Decimal(str(data.get('disk_percent', 0)))
            capacity.tablespace_used_percent = Decimal(str(data.get('tablespace_percent', 0)))
            capacity.available_capacity_gb = Decimal(str(data.get('available_capacity_gb', 0)))
            capacity.largest_table = data.get('largest_table', 'Unknown')
            capacity.growth_gb = growth
            capacity.save()
        else:
            capacity = CapacityPlanning.objects.create(
                month=first_day_of_month,
                database=database_instance,
                database_size_gb=incoming_size,
                disk_used_percent=Decimal(str(data.get('disk_percent', 0))),
                tablespace_used_percent=Decimal(str(data.get('tablespace_percent', 0))),
                available_capacity_gb=Decimal(str(data.get('available_capacity_gb', 0))),
                largest_table=data.get('largest_table', 'Unknown'),
                growth_gb=growth
            )
        return JsonResponse({'status': 'success', 'id': capacity.id})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@csrf_exempt
@require_POST
def incident_ingest(request):
    api_key = request.headers.get('X-API-KEY')
    expected_key = os.environ.get('REPLICATION_API_KEY', 'uptimetracker_api_9f2c1d8e45x9')
    
    if api_key != expected_key:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
        
    try:
        data = json.loads(request.body)
        
        raw_db = data.get('database', 'MySQL')
        if raw_db.strip().lower() in ['mkinsight', 'pentana', 'mssql', 'ms sql', 'ms sql server']:
            db_type = 'MS SQL Server'
            system = data.get('system') or 'MKInsight (Pentana)'
        else:
            db_type = raw_db
            system = data.get('system', f"{db_type} Instance")
        
        start_str = data.get('start')
        end_str = data.get('end')
        
        if not start_str or not end_str:
            return JsonResponse({'error': 'start and end timestamps are required'}, status=400)
            
        def parse_dt(dt_str):
            try:
                dt_str_clean = dt_str.replace(' ', 'T')
                if dt_str_clean.endswith('Z'):
                    dt_str_clean = dt_str_clean[:-1] + '+00:00'
                # If there are milliseconds, strip them or handle them cleanly
                if '.' in dt_str_clean:
                    parts = dt_str_clean.split('.')
                    # Reconstruct without sub-seconds but keep timezone
                    time_part = parts[0]
                    tz_part = ''
                    # Find timezone offset suffix in the second part if present
                    second_part = parts[1]
                    for suffix in ['+', '-']:
                        if suffix in second_part:
                            tz_part = suffix + second_part.split(suffix, 1)[1]
                            break
                    dt_str_clean = time_part + tz_part
                
                return datetime.fromisoformat(dt_str_clean)
            except Exception as e:
                raise ValueError(f"Invalid timestamp format '{dt_str}': {e}")
                
        start_dt = parse_dt(start_str)
        end_dt = parse_dt(end_str)
        
        duration_hrs = calculate_operational_downtime(start_dt, end_dt, db_type)
        
        # De-duplication check: avoid double registering the same outage in staging or active register
        if IncidentStaging.objects.filter(database=db_type, start=start_dt).exists():
            return JsonResponse({'status': 'ignored', 'message': 'Outage already staged'}, status=200)
            
        if IncidentRegister.objects.filter(database=db_type, start=start_dt).exists():
            return JsonResponse({'status': 'ignored', 'message': 'Outage already registered as an active incident'}, status=200)
            
        # Check if this incident occurred within a registered maintenance window for the same database
        maintenance = MaintenanceRegister.objects.filter(
            database=db_type,
            start__lte=start_dt,
            end__gte=end_dt
        ).first()
        
        maint_ticket = None
        root_cause = data.get('root_cause', 'AUTO-LOGGED: Outage detected. Awaiting DBA review.')
        status = 'Awaiting Review'
        maint_record = None
        processed_at = None
        
        if maintenance:
            maint_ticket = maintenance.change_id
            maint_record = maintenance
            status = 'Promoted to Maintenance'
            processed_at = timezone.now()
            root_cause = f"AUTO-LOGGED: Outage occurred within scheduled Maintenance Window ({maint_ticket})."
        else:
            # Highlight that this graceful restart was unplanned
            root_cause = root_cause.replace(
                "DBA Summary: [MAINTENANCE - CLEAN RESTART]",
                "DBA Summary: [UNPLANNED MAINTENANCE - CLEAN RESTART]\n"
                "WARNING: This graceful restart occurred OUTSIDE of a scheduled maintenance window."
            )
            
        staging = IncidentStaging.objects.create(
            database=db_type,
            system=system,
            start=start_dt,
            end=end_dt,
            duration_hrs=duration_hrs,
            root_cause=root_cause,
            status=status,
            maintenance_record=maint_record,
            processed_at=processed_at
        )
        
        return JsonResponse({
            'status': 'success',
            'id': staging.id,
            'maint_linked': maint_ticket is not None
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)



# --- TARGET HOURS VIEWS ---

@login_required
def sla_config_list(request):
    if not request.user.is_admin:
        messages.error(request, 'Only administrators can manage SLA Configurations.')
        return redirect('dashboard')
    
    query = request.GET.get('database', '')
    targets = SLAConfig.objects.all().order_by('-month', 'database')
    if query:
        targets = targets.filter(database__icontains=query)
        
    paginator = Paginator(targets, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'sla_config/list.html', {'page_obj': page_obj, 'query': query})

@login_required
def sla_config_create(request):
    if not request.user.is_admin:
        messages.error(request, 'Only administrators can manage SLA Configurations.')
        return redirect('dashboard')
        
    if request.method == 'POST':
        form = SLAConfigForm(request.POST)
        if form.is_valid():
            target = form.save(commit=False)
            target.created_by = request.user
            target.save()
            messages.success(request, 'SLA Configuration created successfully.')
            return redirect('sla_config_list')
    else:
        form = SLAConfigForm()
    return render(request, 'sla_config/form.html', {'form': form, 'title': 'Create SLA Configuration'})

@login_required
def sla_config_edit(request, pk):
    if not request.user.is_admin:
        messages.error(request, 'Only administrators can manage SLA Configurations.')
        return redirect('dashboard')
        
    target = get_object_or_404(SLAConfig, pk=pk)
    if request.method == 'POST':
        form = SLAConfigForm(request.POST, instance=target)
        if form.is_valid():
            form.save()
            messages.success(request, 'SLA Configuration updated successfully.')
            return redirect('sla_config_list')
    else:
        form = SLAConfigForm(instance=target)
    return render(request, 'sla_config/form.html', {'form': form, 'title': 'Edit SLA Configuration', 'target': target})

@login_required
def sla_config_delete(request, pk):
    if request.method != 'POST':
        return redirect('sla_config_list')
    if not request.user.is_admin:
        messages.error(request, 'Only administrators can manage SLA Configurations.')
        return redirect('dashboard')
        
    target = get_object_or_404(SLAConfig, pk=pk)
    target.delete()
    messages.success(request, 'SLA Configuration deleted successfully.')
    return redirect(request.META.get('HTTP_REFERER', 'sla_config_list'))


# --- UGANDA HOLIDAYS VIEWS ---

@login_required
def holiday_list(request):
    if not request.user.is_admin:
        messages.error(request, 'Only administrators can manage Public Holidays.')
        return redirect('dashboard')
        
    autopopulate_fixed_holidays()
    holidays = UgandaHoliday.objects.all().order_by('-holiday_date')
    paginator = Paginator(holidays, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'holidays/list.html', {'page_obj': page_obj})

@login_required
def holiday_create(request):
    if not request.user.is_admin:
        messages.error(request, 'Only administrators can manage Public Holidays.')
        return redirect('dashboard')
        
    if request.method == 'POST':
        form = UgandaHolidayForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Public Holiday created successfully.')
            return redirect('holiday_list')
    else:
        form = UgandaHolidayForm()
    return render(request, 'holidays/form.html', {'form': form, 'title': 'Create Public Holiday'})

@login_required
def holiday_edit(request, pk):
    if not request.user.is_admin:
        messages.error(request, 'Only administrators can manage Public Holidays.')
        return redirect('dashboard')
        
    holiday = get_object_or_404(UgandaHoliday, pk=pk)
    if holiday.is_fixed:
        messages.error(request, 'Standard public holidays cannot be edited.')
        return redirect('holiday_list')
        
    if request.method == 'POST':
        form = UgandaHolidayForm(request.POST, instance=holiday)
        if form.is_valid():
            form.save()
            messages.success(request, 'Public Holiday updated successfully.')
            return redirect('holiday_list')
    else:
        form = UgandaHolidayForm(instance=holiday)
    return render(request, 'holidays/form.html', {'form': form, 'title': 'Edit Public Holiday', 'holiday': holiday})

@login_required
def holiday_delete(request, pk):
    if request.method != 'POST':
        return redirect('holiday_list')
    if not request.user.is_admin:
        messages.error(request, 'Only administrators can manage Public Holidays.')
        return redirect('dashboard')
        
    holiday = get_object_or_404(UgandaHoliday, pk=pk)
    if holiday.is_fixed:
        messages.error(request, 'Standard public holidays cannot be deleted.')
        return redirect(request.META.get('HTTP_REFERER', 'holiday_list'))
        
    holiday.delete()
    messages.success(request, 'Public Holiday deleted successfully.')
    return redirect(request.META.get('HTTP_REFERER', 'holiday_list'))


@login_required
def incident_staging_list(request):
    if not (request.user.is_admin or request.user.has_perm('api.view_incidentregister')):
        messages.error(request, 'You do not have permission to view incident staging records.')
        return redirect('dashboard')
        
    current_db = request.GET.get('database', '')
    
    # Auto-resolve/sync any staging records that were manually logged or resolved in registers
    awaiting_records = IncidentStaging.objects.filter(status='Awaiting Review')
    for record in awaiting_records:
        incident = IncidentRegister.objects.filter(database=record.database, start=record.start).first()
        if incident:
            record.status = 'Promoted to Incident'
            record.incident_record = incident
            record.processed_at = datetime.now()
            record.save()
            continue
            
        maintenance = MaintenanceRegister.objects.filter(database=record.database, start=record.start).first()
        if maintenance:
            record.status = 'Promoted to Maintenance'
            record.maintenance_record = maintenance
            record.processed_at = datetime.now()
            record.save()
            continue
            
    queryset = IncidentStaging.objects.filter(status='Awaiting Review').order_by('-start')
    
    if current_db:
        queryset = queryset.filter(database=current_db)
        
    paginator = Paginator(queryset, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Calculate waiting duration for each record in the page
    now = timezone.now()
    for record in page_obj:
        delta = now - record.created_at
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        
        if days > 0:
            record.waiting_duration = f"{days}d {hours}h"
        elif hours > 0:
            record.waiting_duration = f"{hours}h {minutes}m"
        else:
            record.waiting_duration = f"{minutes}m"
            
    context = {
        'page_obj': page_obj,
        'current_db': current_db,
    }
    return render(request, 'incidents/staging_list.html', context)


@login_required
def staging_promote_incident(request, pk):
    if not (request.user.is_superuser or request.user.is_staff or request.user.is_admin or request.user.has_perm('api.add_incidentregister')):
        messages.error(request, 'You do not have permission to create incidents.')
        return redirect('incident_staging_list')
        
    staging_item = get_object_or_404(IncidentStaging, pk=pk, status='Awaiting Review')
    
    if request.method == 'POST':
        form = IncidentRegisterForm(request.POST)
        if form.is_valid():
            incident = form.save(commit=False)
            incident.created_by = request.user
            incident.save()
            
            # Link/process the staging record
            staging_item.status = 'Promoted to Incident'
            staging_item.processed_by = request.user
            staging_item.processed_at = timezone.now()
            staging_item.incident_record = incident
            staging_item.save()
            
            messages.success(request, f"Staged alert successfully promoted to Incident: {incident.ticket}")
            return redirect('incident_staging_list')
    else:
        # Determine appropriate severity default from alert text
        severity = 'Medium'
        rc_upper = (staging_item.root_cause or '').upper()
        if 'CRITICAL' in rc_upper:
            severity = 'Critical'
        elif 'HIGH' in rc_upper:
            severity = 'High'
        elif 'LOW' in rc_upper:
            severity = 'Low'

        ticket_id = f"AUTO-SELF-HEAL-{staging_item.start.strftime('%y%m%d-%H%M')}"
        initial = {
            'ticket': ticket_id,
            'date': staging_item.start.date(),
            'start': staging_item.start,
            'end': staging_item.end,
            'severity': severity,
            'system': staging_item.system,
            'database': staging_item.database,
            'root_cause': staging_item.root_cause,
            'resolved_by': 'System',
            'status': 'Resolved',
            'business_impact': 'Awaiting DBA business impact assessment.',
            'assigned_to': request.user.username if request.user.is_authenticated else ''
        }
        form = IncidentRegisterForm(initial=initial)
        
    context = {
        'form': form,
        'title': 'Promote Staging to Incident',
        'staging_item': staging_item
    }
    return render(request, 'incidents/staging_promote_incident.html', context)


@login_required
def staging_promote_maintenance(request, pk):
    if not (request.user.is_superuser or request.user.is_staff or request.user.is_admin or request.user.has_perm('api.add_maintenanceregister')):
        messages.error(request, 'You do not have permission to log maintenance activities.')
        return redirect('incident_staging_list')
        
    staging_item = get_object_or_404(IncidentStaging, pk=pk, status='Awaiting Review')
    
    if request.method == 'POST':
        form = MaintenanceRegisterForm(request.POST)
        if form.is_valid():
            maintenance = form.save(commit=False)
            maintenance.created_by = request.user
            maintenance.save()
            
            # Link/process the staging record
            staging_item.status = 'Promoted to Maintenance'
            staging_item.processed_by = request.user
            staging_item.processed_at = timezone.now()
            staging_item.maintenance_record = maintenance
            staging_item.save()
            
            messages.success(request, f"Staged alert successfully promoted to Maintenance: {maintenance.change_id}")
            return redirect('incident_staging_list')
    else:
        # Pre-populate fields from staging record
        initial = {
            'date': staging_item.start.date(),
            'database': staging_item.database,
            'system': staging_item.system,
            'start': staging_item.start,
            'end': staging_item.end,
            'duration_hrs': staging_item.duration_hrs,
            'activity': staging_item.root_cause,
            'risk': 'Medium',
            'rollback_plan': 'Self-heal recovery / Planned restart verification',
            'approved_by': request.user.username if request.user.is_authenticated else '',
            'status': 'Completed',
            'success': True
        }
        form = MaintenanceRegisterForm(initial=initial)
        
    context = {
        'form': form,
        'title': 'Promote Staging to Maintenance',
        'staging_item': staging_item
    }
    return render(request, 'incidents/staging_promote_maintenance.html', context)


@login_required
def staging_delete(request, pk):
    # Check permissions (admins or users with delete rights)
    if not (request.user.is_superuser or request.user.is_staff or request.user.is_admin or request.user.has_perm('api.delete_incidentregister') or request.user.has_perm('api.delete_incidentstaging')):
        messages.error(request, 'You do not have permission to delete staging records.')
        return redirect('incident_staging_list')
        
    staging_item = get_object_or_404(IncidentStaging, pk=pk, status='Awaiting Review')
    
    if request.method == 'POST':
        staging_item.delete()
        messages.success(request, "Staging alert successfully dismissed.")
        
    return redirect('incident_staging_list')


from django.contrib.auth import update_session_auth_hash
from users.forms import ForcedPasswordChangeForm

@login_required
def password_change_required(request):
    is_forced = getattr(request.user, 'must_change_password', False)

    if request.method == 'POST':
        form = ForcedPasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, request.user)
            messages.success(
                request,
                "Your password has been updated successfully. Welcome to DBTRAC!" if is_forced else "Your password has been changed successfully."
            )
            return redirect('dashboard')
    else:
        form = ForcedPasswordChangeForm(user=request.user)

    return render(request, 'password_change_required.html', {
        'form': form,
        'title': 'Change Password Required - DBTRAC' if is_forced else 'Change Password - DBTRAC',
        'is_forced': is_forced,
    })


from django.contrib.auth import views as auth_views
from django.contrib.auth import login as auth_login
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator

@method_decorator(ensure_csrf_cookie, name='dispatch')
class CustomLoginView(auth_views.LoginView):
    template_name = 'login.html'

    def form_valid(self, form):
        auth_login(self.request, form.get_user())
        user = form.get_user()
        if getattr(user, 'must_change_password', False):
            return redirect('password_change_required')
        return redirect(self.get_success_url())



