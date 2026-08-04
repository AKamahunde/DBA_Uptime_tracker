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
    MaintenanceRegister, CapacityPlanning, SLAConfig, IncidentChangeLog, UgandaHoliday
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

def _get_dashboard_data(reporting_month):
    db_types = ['Oracle', 'MySQL', 'MS SQL Server', 'PostgreSQL']

    def get_status(current, target_val, operator):
        if current == 'No Data' or current is None or current == 'N/A':
            return 'N/A'
        try:
            if isinstance(current, str) and '%' in current:
                current = float(current.replace('%', ''))
            else:
                current = float(current)
            
            if operator == '>=':
                return 'Meets SLA' if current >= target_val else 'Below SLA'
            elif operator == '<=':
                return 'Meets SLA' if current <= target_val else 'Below SLA'
        except:
            return 'N/A'
        return 'N/A'

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
        
        if db_type in ['MS SQL Server', 'PostgreSQL']:
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

    database_detail = None
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
            last_full_str = last_full.date.strftime('%m/%d/%Y') if last_full else 'No Data'
            
            # Last Incremental Backup
            last_inc = BackupRecovery.objects.filter(
                database__iexact=selected_db, record_type='Backup', backup_type='Incremental', outcome='Success'
            ).order_by('-date').first()
            last_inc_str = last_inc.date.strftime('%m/%d/%Y') if last_inc else 'No Data'
            
            # Last DR Test
            last_dr = BackupRecovery.objects.filter(
                database__iexact=selected_db, record_type='Recovery', outcome='Success'
            ).order_by('-date').first()
            last_dr_str = last_dr.date.strftime('%m/%d/%Y') if last_dr else 'No Data'
            
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
                
            if selected_db in ['MS SQL Server', 'PostgreSQL']:
                rep_lag_str = 'N/A'



            # Calculate Statuses
            avail_status = get_status(row_data['availability'], 99.95, '>=') if selected_sla_exists else 'N/A'
            planned_status = get_status(row_data['planned_downtime'], 48.0, '<=') if selected_sla_exists else 'N/A'
            unplanned_status = get_status(row_data['unplanned_downtime'], 2.0, '<=') if selected_sla_exists else 'N/A'
            incidents_status = get_status(row_data['total_incidents'], 4, '<=') if selected_sla_exists else 'N/A'
            mttr_status = get_status(row_data['mttr_mins'], 240, '<=') if selected_sla_exists else 'N/A'
            backup_status = get_status(row_data['backup_success'], 95.0, '>=') if (selected_sla_exists and row_data['backup_success'] != 'No Data') else 'N/A'
            restore_status = get_status(row_data['restore_success'], 80.0, '>=') if (selected_sla_exists and row_data['restore_success'] != 'No Data') else 'N/A'
            
            if row_data['replication_status'] in ['No Data', 'N/A'] or not selected_sla_exists:
                rep_status_check = 'N/A'
            else:
                rep_status_check = 'Meets SLA' if row_data['replication_status'] == 'Healthy' else 'Below SLA'
            
            # Date checks
            now = timezone.now().date()
            if last_full:
                days_since_full = (now - last_full.date).days
                full_status = 'Meets SLA' if days_since_full < 7 else 'Below SLA'
            else:
                full_status = 'N/A'
                
            if last_inc:
                days_since_inc = (now - last_inc.date).days
                inc_status = 'Meets SLA' if days_since_inc <= 1 else 'Below SLA' # 24 hrs
            else:
                inc_status = 'N/A'
                
            if last_dr:
                days_since_dr = (now - last_dr.date).days
                dr_status = 'Meets SLA' if days_since_dr <= 180 else 'Below SLA'
            else:
                dr_status = 'N/A'

            failed_backup_status = 'Meets SLA' if (row_data['failed_backups'] == 0 and selected_sla_exists) else ('Below SLA' if selected_sla_exists else 'N/A')
            failed_restore_status = 'Meets SLA' if (failed_restores == 0 and selected_sla_exists) else ('Below SLA' if selected_sla_exists else 'N/A')
            
            rep_lag_status = 'Below SLA'
            if rep_lag not in ['No Data', 'N/A'] and selected_sla_exists:
                rep_lag_status = 'Meets SLA' if (rep_lag / 60) < 2 else 'Below SLA'
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

    dashboard_data, _ = _get_dashboard_data(reporting_month)
    
    context = {
        'dashboard_data': dashboard_data,
        'reporting_month_str': reporting_month.strftime('%B %Y'),
        'extracted_by': request.user.username,
        'extracted_at': timezone.now().strftime('%Y-%m-%d %H:%M:%S'),
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

    dashboard_data, _ = _get_dashboard_data(reporting_month)
    month_display = reporting_month.strftime('%B %Y')

    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        
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
            
        # Add Legend / Key below table
        ws.append([]) # empty
        ws.append([]) # empty
        legend_start = row_num + 2
        
        ws.cell(row=legend_start, column=1, value="Database Key & Host Systems:").font = font_bold
        ws.cell(row=legend_start+1, column=1, value="• Oracle: nmsinfra.nms.go.ug (DB) & nmsebs.nms.go.ug (App) - This is the NMS ERP and Core Infrastructure database.").font = font_body
        ws.cell(row=legend_start+2, column=1, value="• MySQL: nickledbnew (with replication on nickeldbdr) - Houses systems like NMS360, ServiceDesk, and DMT.").font = font_body
        ws.cell(row=legend_start+3, column=1, value="• MS SQL Server: Windows Server - Houses MKInsight used by auditors.").font = font_body
        ws.cell(row=legend_start+4, column=1, value="• PostgreSQL: Localhost / PostgreSQL Instance - Houses ColdChain System ODKX.").font = font_body
        
        # Adjust Column Widths
        for col in ws.columns:
            max_len = 0
            col_letter = col[0].column_letter
            for cell in col:
                if cell.row == 1 or cell.row > legend_start:
                    continue
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)
            
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="DB_Performance_Summary_{month_str}.xlsx"'
        wb.save(response)
        return response
        
    except ImportError:
        import csv
        from django.http import HttpResponse
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="DB_Performance_Summary_{month_str}.csv"'
        
        writer = csv.writer(response)
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
        writer.writerow([])
        writer.writerow(["Database Key & Host Systems:"])
        writer.writerow(["Oracle: nmsinfra.nms.go.ug (DB) & nmsebs.nms.go.ug (App) - This is the NMS ERP and Core Infrastructure database."])
        writer.writerow(["MySQL: nickledbnew (with replication on nickeldbdr) - Houses systems like NMS360, ServiceDesk, and DMT."])
        writer.writerow(["MS SQL Server: Windows Server - Houses MKInsight used by auditors."])
        writer.writerow(["PostgreSQL: Localhost / PostgreSQL Instance - Houses ColdChain System ODKX."])
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
    queryset = CapacityPlanning.objects.all().order_by('-month')
    
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
    
    return render(request, 'capacity/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'start_date': start_date,
        'end_date': end_date
    })

@login_required
def capacity_create(request):
    if not (request.user.is_admin or request.user.has_perm('api.add_capacityplanning')):
        messages.error(request, 'You do not have permission to create capacity planning records.')
        return redirect('capacity_list')

    if request.method == 'POST':
        form = CapacityPlanningForm(request.POST)
        if form.is_valid():
            capacity = form.save(commit=False)
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
        
        # Enforce a single capacity record per database per month by truncating to the 1st
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
        
        # De-duplication check: avoid double registering the same outage
        exists = IncidentRegister.objects.filter(database=db_type, start=start_dt).exists()
        if exists:
            return JsonResponse({'status': 'ignored', 'message': 'Incident already registered'}, status=200)
            
        # Check if this incident occurred within a registered maintenance window for the same database
        maintenance = MaintenanceRegister.objects.filter(
            database=db_type,
            start__lte=start_dt,
            end__gte=end_dt
        ).first()
        
        is_planned = False
        maint_ticket = None
        severity = 'Medium'
        root_cause = data.get('root_cause', 'AUTO-LOGGED: MySQL restart detected in systemd journal. Awaiting DBA review.')
        
        if maintenance:
            is_planned = True
            maint_ticket = maintenance.change_id
            severity = 'Low'
            root_cause = f"AUTO-LOGGED: MySQL restart detected within scheduled Maintenance Window ({maint_ticket})."
        else:
            # Highlight that this graceful restart was unplanned
            root_cause = root_cause.replace(
                "DBA Summary: [MAINTENANCE - CLEAN RESTART]",
                "DBA Summary: [UNPLANNED MAINTENANCE - CLEAN RESTART]\n"
                "WARNING: This graceful restart occurred OUTSIDE of a scheduled maintenance window."
            )
            
        # Build unique self-heal ticket ID e.g., AUTO-SELF-HEAL-260730-1000
        ticket_id = f"AUTO-SELF-HEAL-{start_dt.strftime('%y%m%d-%H%M')}"
        
        incident = IncidentRegister.objects.create(
            ticket=ticket_id,
            date=start_dt.date(),
            start=start_dt,
            end=end_dt,
            duration_hrs=duration_hrs,
            planned=is_planned,
            severity=severity,
            system=system,
            root_cause=root_cause,
            business_impact='Awaiting DBA business impact assessment.' if not is_planned else 'None - Scheduled Maintenance.',
            resolved_by='System',
            status='Resolved',
            database=db_type,
            assigned_to='DBA Team'
        )
        
        return JsonResponse({'status': 'success', 'id': incident.id, 'ticket': ticket_id})
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


