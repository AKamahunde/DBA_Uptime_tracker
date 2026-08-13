from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
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
                diff = now - inc.start
                unplanned += float(diff.total_seconds() / 3600.0)
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

        dashboard_data.append({
            'database': db_type,
            'availability': avail_str,
            'availability_status': get_status(avail_str, 99.95, '>=') if sla_exists else 'N/A',
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
            'failed_backups': failed_backups if sla_exists else 'No Data'
        })
    return dashboard_data, db_types

def _get_database_detail(selected_db, reporting_month, dashboard_data):
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
                dr_status = 'Meets SLA' if days_since_dr <= 150 else ('Warning' if days_since_dr <= 180 else 'Below SLA')
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
        avg_avail = round(sum(t_avails)/len(t_avails), 2) if t_avails else 100.0
        trend_months.append({
            'name': t_month.strftime('%b'),
            'value': avg_avail,
        })
        
    min_val = min([m['value'] for m in trend_months] + [99.95])
    y_min = math.floor(min_val - 0.2) if min_val < 98.0 else 98.0
    y_max = 100.0
    y_range = max(0.1, y_max - y_min)
    
    points = []
    for idx, m in enumerate(trend_months):
        x = 40 + idx * 64
        y = 20 + (100.0 - m['value']) * 80 / y_range
        points.append(f"{x},{y}")
        m['x'] = x
        m['y'] = y
        m['text_y'] = y - 8
    trend_polyline_points = " ".join(points)
    sla_y_coord = 20 + (100.0 - 99.95) * 80 / y_range
    
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

    context = {
        'incident_count': row[0],
        'replication_count': row[1],
        'backup_count': row[2],
        'server_health_count': row[3],
        'maintenance_count': row[4],
        'capacity_count': row[5],
        'is_admin': request.user.is_admin,
        'dashboard_data': dashboard_data,
        'database_detail': database_detail,
        'selected_db': selected_db,
        'reporting_month': reporting_month.strftime('%Y-%m'),
        
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
        'trend_polyline_points': trend_polyline_points,
        'sla_y_coord': sla_y_coord,
        'y_min': y_min,
        'y_max': y_max,
        'attention_items': attention_items,
        'recent_activity': recent_activity,
    }
    return render(request, 'dashboard.html', context)

@login_required
def dashboard_print(request):
    month_str = request.GET.get('month', '')
    if month_str:
        try:
            reporting_month = datetime.strptime(month_str, '%Y-%m').date()
        except ValueError:
            reporting_month = timezone.now().date().replace(day=1)
    else:
        reporting_month = timezone.now().date().replace(day=1)

    selected_db = request.GET.get('database_select', 'All Databases')
    dashboard_data, _ = _get_dashboard_data(reporting_month)
    database_detail = _get_database_detail(selected_db, reporting_month, dashboard_data)
    
    user_display = request.user.get_full_name()
    if not user_display:
        user_display = request.user.username.replace('.', ' ').title()

    context = {
        'dashboard_data': dashboard_data,
        'reporting_month_str': reporting_month.strftime('%B %Y'),
        'extracted_by': user_display,
        'extracted_at': timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M:%S'),
        'database_detail': database_detail,
        'selected_db': selected_db,
    }
    return render(request, 'dashboard_print.html', context)

@login_required
def dashboard_excel(request):
    month_str = request.GET.get('month', '')
    if month_str:
        try:
            reporting_month = datetime.strptime(month_str, '%Y-%m').date()
        except ValueError:
            reporting_month = timezone.now().date().replace(day=1)
    else:
        reporting_month = timezone.now().date().replace(day=1)

    selected_db = request.GET.get('database_select', 'All Databases')
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
        ws.merge_cells('A1:J1')
        if selected_db and selected_db != 'All Databases':
            ws['A1'] = f"Summary of Database Performance for {selected_db} - {month_display}"
        else:
            ws['A1'] = f"Summary of Database Performance - {month_display}"
        ws['A1'].font = font_title
        ws['A1'].alignment = align_left
        ws.row_dimensions[1].height = 30
        
        ws.append([]) # Empty row
        
        # Add Headers
        headers = [
            "Database", "SLA Uptime", "Planned Downtime (Hrs)", "Unplanned Downtime (Hrs)",
            "Total Incidents", "SLA Breaches", "MTTR (Mins)", 
            "Backup Success %", "Restore Success %", "Replication Status"
        ]
        ws.append(headers)
        ws.row_dimensions[3].height = 25
        
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=3, column=col_num)
            cell.font = font_header
            cell.fill = fill_header
            cell.alignment = align_center
            cell.border = border_thin
            
        # Add Data rows
        row_num = 4
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
        ws.cell(row=legend_start+1, column=1, value="• Oracle: nmsinfra.nms.go.ug (DB) & nmsebs.nms.go.ug (App) - Powers the core NMS ERP suite and foundational infrastructure operations.").font = font_body
        ws.cell(row=legend_start+2, column=1, value="• MySQL: nickledbnew (replicated live to nickeldbdr) - Hosts vital collaboration and support systems including the NMS360 suite, IT ServiceDesk, and the Delivery Monitoring Tool (DMT).").font = font_body
        ws.cell(row=legend_start+3, column=1, value="• MS SQL Server: Windows Server host - Hosts MKInsight, which runs Pentana Audit, the system used by internal auditors.").font = font_body
        
        # Adjust Column Widths
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
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
        from django.http import HttpResponse
        filename_prefix = f"DB_Performance_Summary_{selected_db.replace(' ', '_')}_{month_str}" if selected_db and selected_db != 'All Databases' else f"DB_Performance_Summary_{month_str}"
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename_prefix}.csv"'
        
        writer = csv.writer(response)
        if selected_db and selected_db != 'All Databases':
            writer.writerow([f"Summary of Database Performance for {selected_db} - {month_display}"])
        else:
            writer.writerow([f"Summary of Database Performance - {month_display}"])
        writer.writerow([])
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
        writer.writerow(["Oracle: nmsinfra.nms.go.ug (DB) & nmsebs.nms.go.ug (App) - Powers the core NMS ERP suite and foundational infrastructure operations."])
        writer.writerow(["MySQL: nickledbnew (replicated live to nickeldbdr) - Hosts vital collaboration and support systems including the NMS360 suite, IT ServiceDesk, and the Delivery Monitoring Tool (DMT)."])
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
    
    return render(request, 'incidents/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'start_date': start_date,
        'end_date': end_date
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
    
    return render(request, 'replication/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'start_date': start_date,
        'end_date': end_date
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
    
    return render(request, 'backups/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'start_date': start_date,
        'end_date': end_date
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
    
    can_edit_always = request.user.is_admin
    is_creator_within_time = (backup.created_by == request.user) and ((timezone.now() - backup.created_at).total_seconds() < 300)
    
    if not (can_edit_always or is_creator_within_time):
        messages.error(request, 'You can only edit your own records within 5 minutes of creation.')
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
def server_health_list(request):
    if not (request.user.is_admin or request.user.has_perm('api.view_serverhealth')):
        messages.error(request, 'You do not have permission to view server health records.')
        return redirect('dashboard')
    queryset = ServerHealth.objects.all().order_by('-date', '-updated_at')
    
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
    
    return render(request, 'server_health/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'start_date': start_date,
        'end_date': end_date
    })

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
    
    if health.entered_by == 'Script':
        messages.error(request, 'Server health records generated by automated scripts cannot be edited.')
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
    
    return render(request, 'maintenance/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'start_date': start_date,
        'end_date': end_date
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
    forecast_db = current_db if current_db else 'Oracle'
    db_recs = CapacityPlanning.objects.filter(database=forecast_db).order_by('-month', '-updated_at')[:3]
    forecast_info = {}
    if db_recs.exists():
        latest = db_recs[0]
        avg_growth = sum(float(r.growth_gb) for r in db_recs) / len(db_recs)
        
        cap = float(latest.disk_capacity_gb)
        used_pct = float(latest.disk_used_percent)
        free = float(latest.available_capacity_gb)
        
        if cap == 0.0 and free == 0.0 and used_pct > 0:
            size = float(latest.database_size_gb)
            cap = size * 5.0 if used_pct == 20 else size / (used_pct/100.0)
            free = cap - size
            
        warning_used_gb = cap * 0.8
        current_used_gb = cap * used_pct / 100.0
        space_left_gb = warning_used_gb - current_used_gb
        
        if avg_growth > 0:
            headroom_months = space_left_gb / avg_growth
            if headroom_months < 0:
                headroom_str = "Exceeded threshold"
                est_date_str = "Exceeded"
            elif headroom_months > 60:
                headroom_str = "> 5 years"
                est_date_str = "Safe"
            else:
                headroom_str = f"{round(headroom_months, 1)} months"
                # Calculate estimated date
                est_days = headroom_months * 30.44
                est_date = latest.month + timedelta(days=est_days)
                est_date_str = est_date.strftime('%B %Y')
        else:
            headroom_str = "Infinite"
            est_date_str = "Safe"
            
        forecast_info = {
            'database': forecast_db,
            'current_used_pct': round(used_pct, 1),
            'avg_growth': round(avg_growth, 2),
            'headroom_months': headroom_str,
            'est_date': est_date_str
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
            col_letter = get_column_letter(col[0].column)
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
        from django.http import HttpResponse
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
    expected_key = os.environ.get('REPLICATION_API_KEY')
    
    if not expected_key or api_key != expected_key:
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
    expected_key = os.environ.get('REPLICATION_API_KEY')  # We can reuse the same API key
    
    if not expected_key or api_key != expected_key:
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
            
        backup = BackupRecovery.objects.create(
            date=start_dt.date(),
            record_type='Backup',
            database=data.get('database', 'MySQL'),
            database_instance=data.get('database_instance', 'Unknown'),
            backup_type=data.get('backup_type', 'Incremental'),
            backup_used=data.get('backup_used', 'N/A'),
            start=start_dt,
            end=data.get('end'),
            duration_mins=data.get('duration_mins', 0),
            outcome=data.get('outcome', 'Failed'),
            failure_reason=data.get('failure_reason', ''),
            location=data.get('location', 'Unknown'),
            verified=data.get('verified', False),
            compression=data.get('compression', True),
        )
        return JsonResponse({'status': 'success', 'id': backup.id})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)

@csrf_exempt
@require_POST
def server_health_ingest(request):
    api_key = request.headers.get('X-API-KEY')
    expected_key = os.environ.get('REPLICATION_API_KEY')
    
    if not expected_key or api_key != expected_key:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
        
    try:
        data = json.loads(request.body)
        
        # Calculate status based on thresholds
        cpu = float(data.get('cpu_percent', 0))
        memory = float(data.get('memory_percent', 0))
        disk = float(data.get('disk_percent', 0))
        
        status = 'Healthy'
        if cpu > 90 or memory > 90 or disk > 90:
            status = 'Critical'
        elif cpu > 75 or memory > 80 or disk > 80:
            status = 'Warning'
            
        # Check for optional custom date override (for backfilling)
        date_str = data.get('date')
        if date_str:
            health_date = datetime.strptime(date_str, "%Y-%m-%d").date()
          
        else:
            health_date = datetime.now().date()
            
        # Find the first existing entry for this server/date combination (handles pre-existing duplicate logs cleanly)
        health = ServerHealth.objects.filter(
            date=health_date,
            server=data.get('server', 'Unknown'),
            database=data.get('database', 'MySQL')
        ).first()
        
        if health:
            health.entered_by = 'Script'
            health.cpu_percent = cpu
            health.memory_percent = memory
            health.disk_percent = disk
            health.tablespace_percent = float(data.get('tablespace_percent', 0))
            health.connections = int(data.get('connections', 0))
            health.slow_queries = int(data.get('slow_queries', 0))
            health.threads = int(data.get('threads', 0))
            health.status = status
            health.save()
        else:
            health = ServerHealth.objects.create(
                date=health_date,
                server=data.get('server', 'Unknown'),
                database=data.get('database', 'MySQL'),
                entered_by='Script',
                cpu_percent=cpu,
                memory_percent=memory,
                disk_percent=disk,
                tablespace_percent=float(data.get('tablespace_percent', 0)),
                connections=int(data.get('connections', 0)),
                slow_queries=int(data.get('slow_queries', 0)),
                threads=int(data.get('threads', 0)),
                status=status
            )
        return JsonResponse({'status': 'success', 'id': health.id})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)
 
@csrf_exempt
@require_POST
def capacity_ingest(request):
    api_key = request.headers.get('X-API-KEY')
    expected_key = os.environ.get('REPLICATION_API_KEY')
    
    if not expected_key or api_key != expected_key:
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
    expected_key = os.environ.get('REPLICATION_API_KEY')
    
    if not expected_key or api_key != expected_key:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
        
    try:
        data = json.loads(request.body)
        
        db_type = data.get('database', 'MySQL')
        start_str = data.get('start')
        end_str = data.get('end')
        system = data.get('system', 'MySQL Instance')
        
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
        
        duration_delta = end_dt - start_dt
        duration_hrs = Decimal(str(round(duration_delta.total_seconds() / 3600.0, 2)))
        
        # De-duplication check: avoid double registering the same outage in staging
        exists = IncidentStaging.objects.filter(database=db_type, start=start_dt).exists()
        if exists:
            return JsonResponse({'status': 'ignored', 'message': 'Outage already staged'}, status=200)
            
        # Check if this incident occurred within a registered maintenance window for the same database
        maintenance = MaintenanceRegister.objects.filter(
            database=db_type,
            start__lte=start_dt,
            end__gte=end_dt
        ).first()
        
        maint_ticket = None
        root_cause = data.get('root_cause', 'AUTO-LOGGED: MySQL restart detected in systemd journal. Awaiting DBA review.')
        
        if maintenance:
            maint_ticket = maintenance.change_id
            root_cause = f"AUTO-LOGGED: MySQL restart detected within scheduled Maintenance Window ({maint_ticket})."
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
            status='Awaiting Review'
        )
        
        return JsonResponse({'status': 'success', 'id': staging.id})
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
    
    queryset = IncidentStaging.objects.filter(status='Awaiting Review').order_by('-start')
    
    if current_db:
        queryset = queryset.filter(database=current_db)
        
    paginator = Paginator(queryset, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'current_db': current_db,
    }
    return render(request, 'incidents/staging_list.html', context)


@login_required
def staging_promote_incident(request, pk):
    if not (request.user.is_admin or request.user.has_perm('api.add_incidentregister')):
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
        # Pre-populate fields from the staging record
        ticket_id = f"AUTO-SELF-HEAL-{staging_item.start.strftime('%y%m%d-%H%M')}"
        initial = {
            'ticket': ticket_id,
            'date': staging_item.start.date(),
            'start': staging_item.start,
            'end': staging_item.end,
            'system': staging_item.system,
            'database': staging_item.database,
            'root_cause': staging_item.root_cause,
            'resolved_by': 'System',
            'status': 'Resolved',
            'business_impact': 'Awaiting DBA business impact assessment.'
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
    if not (request.user.is_admin or request.user.has_perm('api.add_maintenanceregister')):
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
            'activity': staging_item.root_cause,
            'success': True
        }
        form = MaintenanceRegisterForm(initial=initial)
        
    context = {
        'form': form,
        'title': 'Promote Staging to Maintenance',
        'staging_item': staging_item
    }
    return render(request, 'incidents/staging_promote_maintenance.html', context)


