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
    MaintenanceRegister, CapacityPlanning, TargetHours, IncidentChangeLog
)
from .forms import (
    IncidentRegisterForm, ReplicationMonitorForm, BackupRecoveryForm,
    ServerHealthForm, MaintenanceRegisterForm, CapacityPlanningForm
)
from users.models import User
from django.db.models import Sum, Count, Avg

@login_required
def about(request):
    return render(request, 'about.html')



@login_required
def dashboard(request):
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
    db_types = ['Oracle', 'MySQL', 'MS SQL Server', 'PostgreSQL']
    dashboard_data = []

    for db_type in db_types:
        # Planned Downtime
        planned = MaintenanceRegister.objects.filter(
            database__iexact=db_type, success=True, date__year=reporting_month.year, date__month=reporting_month.month
        ).aggregate(Sum('duration_hrs'))['duration_hrs__sum'] or 0

        # Unplanned Downtime & MTTR & Incidents
        incidents = IncidentRegister.objects.filter(
            database__iexact=db_type, date__year=reporting_month.year, date__month=reporting_month.month
        )
        total_incidents = incidents.count()
        
        unplanned = 0
        now = timezone.now()
        for inc in incidents:
            if not inc.end and inc.status not in ['Resolved', 'Closed'] and inc.start:
                diff = now - inc.start
                unplanned += float(diff.total_seconds() / 3600.0)
            elif inc.duration_hrs:
                unplanned += float(inc.duration_hrs)
                
        mttr_hrs = (unplanned / total_incidents) if total_incidents > 0 else 0
        mttr_mins = round(mttr_hrs * 60, 2)

        # Target Hours (Availability)
        th = TargetHours.objects.filter(
            database_type__iexact=db_type.replace(' ', ''), month__year=reporting_month.year, month__month=reporting_month.month
        ).aggregate(Sum('target_hours'))['target_hours__sum']
        
        # fallback to standard hours if no target hours are set
        if not th:
            th = TargetHours.objects.filter(
                database_type__iexact=db_type, month__year=reporting_month.year, month__month=reporting_month.month
            ).aggregate(Sum('target_hours'))['target_hours__sum']
        
        if not th:
            th = 730

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

        # Replication Status (Latest record for this month)
        latest_rep = ReplicationMonitor.objects.filter(
            database__iexact=db_type, date__year=reporting_month.year, date__month=reporting_month.month
        ).order_by('-date', '-created_at').first()
        rep_status = latest_rep.status if latest_rep else 'No Data'
        
        if db_type in ['MS SQL Server', 'PostgreSQL']:
            rep_status = 'N/A'

        dashboard_data.append({
            'database': db_type,
            'availability': avail_str,
            'planned_downtime': round(planned, 2),
            'unplanned_downtime': round(unplanned, 2),
            'total_incidents': total_incidents,
            'mttr_mins': mttr_mins,
            'backup_success': backup_success_pct,
            'restore_success': restore_success_pct,
            'replication_status': rep_status,
            'failed_backups': failed_backups
        })

    database_detail = None
    if selected_db in db_types:
        row_data = next((item for item in dashboard_data if item['database'] == selected_db), None)
        if row_data:
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

            def get_status(current, target_val, operator):
                if current == 'No Data' or current is None:
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

            # Calculate Statuses
            avail_status = get_status(row_data['availability'], 99.50, '>=')
            planned_status = get_status(row_data['planned_downtime'], 48.0, '<=')
            unplanned_status = get_status(row_data['unplanned_downtime'], 2.0, '<=')
            incidents_status = get_status(row_data['total_incidents'], 4, '<=')
            mttr_status = get_status(row_data['mttr_mins'], 240, '<=') # 4 hrs = 240 mins
            backup_status = get_status(row_data['backup_success'], 95.0, '>=')
            restore_status = get_status(row_data['restore_success'], 80.0, '>=')
            
            if row_data['replication_status'] in ['No Data', 'N/A']:
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
                months_since_dr = (now.year - last_dr.date.year) * 12 + now.month - last_dr.date.month
                dr_status = 'Meets SLA' if months_since_dr < 6 else 'Below SLA'
            else:
                dr_status = 'N/A'

            failed_backup_status = 'Meets SLA' if row_data['failed_backups'] == 0 else 'Below SLA'
            failed_restore_status = 'Meets SLA' if failed_restores == 0 else 'Below SLA'
            
            rep_lag_status = 'Below SLA'
            if rep_lag not in ['No Data', 'N/A']:
                rep_lag_status = 'Meets SLA' if (rep_lag / 60) < 2 else 'Below SLA'
            elif row_data['replication_status'] in ['No Data', 'N/A']:
                rep_lag_status = 'N/A'

            database_detail = {
                'database': selected_db,
                'current': {
                    'availability': row_data['availability'],
                    'planned_downtime': row_data['planned_downtime'],
                    'unplanned_downtime': row_data['unplanned_downtime'],
                    'total_incidents': row_data['total_incidents'],
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
def incident_list(request):
    if not (request.user.is_admin or request.user.has_perm('api.view_incidentregister')):
        messages.error(request, 'You do not have permission to view incident records.')
        return redirect('dashboard')
    queryset = IncidentRegister.objects.prefetch_related('change_logs', 'change_logs__changed_by').all().order_by('-date', '-created_at')
    
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
    queryset = ServerHealth.objects.all().order_by('-date', '-created_at')
    
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
@require_POST
def incident_delete(request, pk):
    obj = get_object_or_404(IncidentRegister, pk=pk)
    
    can_delete_always = request.user.is_admin
    is_creator_within_time = (obj.created_by == request.user) and ((timezone.now() - obj.created_at).total_seconds() < 300)
    
    if not (can_delete_always or is_creator_within_time):
        messages.error(request, 'You can only delete your own records within 5 minutes of creation.')
        return redirect('incident_list')
            
    obj.delete()
    messages.success(request, 'Incident record deleted successfully.')
    return redirect('incident_list')

@login_required
@require_POST
def replication_delete(request, pk):
    obj = get_object_or_404(ReplicationMonitor, pk=pk)
    
    can_delete_always = request.user.is_admin
    is_creator_within_time = (obj.created_by == request.user) and ((timezone.now() - obj.created_at).total_seconds() < 300)
    
    if not (can_delete_always or is_creator_within_time):
        messages.error(request, 'You can only delete your own records within 5 minutes of creation.')
        return redirect('replication_list')
            
    obj.delete()
    messages.success(request, 'Replication record deleted successfully.')
    return redirect('replication_list')

@login_required
@require_POST
def backup_delete(request, pk):
    obj = get_object_or_404(BackupRecovery, pk=pk)
    
    can_delete_always = request.user.is_admin
    is_creator_within_time = (obj.created_by == request.user) and ((timezone.now() - obj.created_at).total_seconds() < 300)
    
    if not (can_delete_always or is_creator_within_time):
        messages.error(request, 'You can only delete your own records within 5 minutes of creation.')
        return redirect('backup_list')
            
    obj.delete()
    messages.success(request, 'Backup record deleted successfully.')
    return redirect('backup_list')

@login_required
@require_POST
def server_health_delete(request, pk):
    obj = get_object_or_404(ServerHealth, pk=pk)
    
    can_delete_always = request.user.is_admin
    is_creator_within_time = (obj.created_by == request.user) and ((timezone.now() - obj.created_at).total_seconds() < 300)
    
    if not (can_delete_always or is_creator_within_time):
        messages.error(request, 'You can only delete your own records within 5 minutes of creation.')
        return redirect('server_health_list')
            
    obj.delete()
    messages.success(request, 'Server health record deleted successfully.')
    return redirect('server_health_list')

@login_required
@require_POST
def maintenance_delete(request, pk):
    obj = get_object_or_404(MaintenanceRegister, pk=pk)
    
    can_delete_always = request.user.is_admin
    is_creator_within_time = (obj.created_by == request.user) and ((timezone.now() - obj.created_at).total_seconds() < 300)
    
    if not (can_delete_always or is_creator_within_time):
        messages.error(request, 'You can only delete your own records within 5 minutes of creation.')
        return redirect('maintenance_list')
            
    obj.delete()
    messages.success(request, 'Maintenance record deleted successfully.')
    return redirect('maintenance_list')

@login_required
@require_POST
def capacity_delete(request, pk):
    obj = get_object_or_404(CapacityPlanning, pk=pk)
    
    can_delete_always = request.user.is_admin
    is_creator_within_time = (obj.created_by == request.user) and ((timezone.now() - obj.created_at).total_seconds() < 300)
    
    if not (can_delete_always or is_creator_within_time):
        messages.error(request, 'You can only delete your own records within 5 minutes of creation.')
        return redirect('capacity_list')
            
    obj.delete()
    messages.success(request, 'Capacity planning record deleted successfully.')
    return redirect('capacity_list')


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
        
        backup = BackupRecovery.objects.create(
            date=start_dt.date(),
            record_type='Backup',
            database=data.get('database', 'MySQL'),
            database_instance=data.get('database_instance', 'Unknown'),
            backup_type=data.get('backup_type', 'Incremental'),
            backup_used=data.get('backup_used', 'N/A'),
            start=start_str,
            end=data.get('end'),
            duration_mins=data.get('duration_mins', 0),
            outcome=data.get('outcome', 'Failed'),
            location=data.get('location', 'Unknown'),
            verified=data.get('verified', False),
            compression=data.get('compression', True),
            retention=data.get('retention', '7 Days')
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
            
        health = ServerHealth.objects.create(
            date=datetime.now().date(),
            server=data.get('server', 'Unknown'),
            entered_by='Script',
            cpu_percent=cpu,
            memory_percent=memory,
            disk_percent=disk,
            tablespace_percent=float(data.get('tablespace_percent', 0)),
            connections=int(data.get('connections', 0)),
            slow_queries=int(data.get('slow_queries', 0)),
            threads=int(data.get('threads', 0)),
            status=status,
            database=data.get('database', 'MySQL')
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
        
        # Calculate growth by finding the most recent record for this database
        most_recent = CapacityPlanning.objects.filter(database=database_instance).order_by('-month').first()
        growth = Decimal('0.00')
        if most_recent:
            growth = incoming_size - most_recent.database_size_gb
            
        capacity = CapacityPlanning.objects.create(
            month=datetime.now().date(),
            database_size_gb=incoming_size,
            disk_used_percent=Decimal(str(data.get('disk_percent', 0))),
            tablespace_used_percent=Decimal(str(data.get('tablespace_percent', 0))),
            largest_table=data.get('largest_table', 'Unknown'),
            growth_gb=growth,
            database=database_instance
        )
        return JsonResponse({'status': 'success', 'id': capacity.id})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)
