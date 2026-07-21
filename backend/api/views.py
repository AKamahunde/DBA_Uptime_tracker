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
from datetime import timedelta
from .models import (
    IncidentRegister, ReplicationMonitor, BackupRecovery, ServerHealth,
    MaintenanceRegister, CapacityPlanning
)
from .forms import (
    IncidentRegisterForm, ReplicationMonitorForm, BackupRecoveryForm,
    ServerHealthForm, MaintenanceRegisterForm, CapacityPlanningForm
)
from users.models import User

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

    context = {
        'incident_count': row[0],
        'replication_count': row[1],
        'backup_count': row[2],
        'server_health_count': row[3],
        'maintenance_count': row[4],
        'capacity_count': row[5],
        'is_admin': request.user.is_admin,
    }
    return render(request, 'dashboard.html', context)

@login_required
def incident_list(request):
    if not (request.user.is_admin or request.user.has_perm('api.view_incidentregister')):
        messages.error(request, 'You do not have permission to view incident records.')
        return redirect('dashboard')
    queryset = IncidentRegister.objects.all().order_by('-date')
    
    current_db = request.GET.get('database', '')
    current_date = request.GET.get('date', '')
    if current_db:
        queryset = queryset.filter(database=current_db)
    if current_date:
        queryset = queryset.filter(date=current_date)
        
    paginator = Paginator(queryset, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'incidents/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'current_date': current_date
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
            messages.success(request, 'Incident created successfully.')
            return redirect('incident_list')
    else:
        form = IncidentRegisterForm()
    return render(request, 'incidents/form.html', {'form': form, 'title': 'Create Incident'})

@login_required
def incident_edit(request, pk):
    incident = get_object_or_404(IncidentRegister, pk=pk)
    
    can_edit_always = request.user.is_admin or request.user.has_perm('api.change_incidentregister')
    is_creator_within_time = (incident.created_by == request.user) and ((timezone.now() - incident.created_at).total_seconds() < 300)
    
    if not (can_edit_always or is_creator_within_time):
        messages.error(request, 'You can only edit your own records within 5 minutes of creation.')
        return redirect('incident_list')
    
    if request.method == 'POST':
        form = IncidentRegisterForm(request.POST, instance=incident)
        if form.is_valid():
            form.save()
            messages.success(request, 'Incident updated successfully.')
            return redirect('incident_list')
    else:
        form = IncidentRegisterForm(instance=incident)
    
    return render(request, 'incidents/form.html', {
        'form': form, 
        'title': 'Edit Incident',
        'can_edit': True,
        'incident': incident
    })

@login_required
def replication_list(request):
    if not (request.user.is_admin or request.user.has_perm('api.view_replicationmonitor')):
        messages.error(request, 'You do not have permission to view replication records.')
        return redirect('dashboard')
    queryset = ReplicationMonitor.objects.all().order_by('-date')
    
    current_db = request.GET.get('database', '')
    current_date = request.GET.get('date', '')
    if current_db:
        queryset = queryset.filter(database=current_db)
    if current_date:
        queryset = queryset.filter(date=current_date)
        
    paginator = Paginator(queryset, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'replication/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'current_date': current_date
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
    
    can_edit_always = request.user.is_admin or request.user.has_perm('api.change_replicationmonitor')
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
    queryset = BackupRecovery.objects.all().order_by('-date')
    
    current_db = request.GET.get('database', '')
    current_date = request.GET.get('date', '')
    if current_db:
        queryset = queryset.filter(database=current_db)
    if current_date:
        queryset = queryset.filter(date=current_date)
        
    paginator = Paginator(queryset, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'backups/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'current_date': current_date
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
    return render(request, 'backups/form.html', {'form': form, 'title': 'Create Backup Record'})

@login_required
def backup_edit(request, pk):
    backup = get_object_or_404(BackupRecovery, pk=pk)
    
    can_edit_always = request.user.is_admin or request.user.has_perm('api.change_backuprecovery')
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
    queryset = ServerHealth.objects.all().order_by('-date')
    
    current_db = request.GET.get('database', '')
    current_date = request.GET.get('date', '')
    if current_db:
        queryset = queryset.filter(database=current_db)
    if current_date:
        queryset = queryset.filter(date=current_date)
        
    paginator = Paginator(queryset, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'server_health/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'current_date': current_date
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
    
    can_edit_always = request.user.is_admin or request.user.has_perm('api.change_serverhealth')
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
    queryset = MaintenanceRegister.objects.all().order_by('-date')
    
    current_db = request.GET.get('database', '')
    current_date = request.GET.get('date', '')
    if current_db:
        queryset = queryset.filter(database=current_db)
    if current_date:
        queryset = queryset.filter(date=current_date)
        
    paginator = Paginator(queryset, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'maintenance/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'current_date': current_date
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
    
    can_edit_always = request.user.is_admin or request.user.has_perm('api.change_maintenanceregister')
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
    current_date = request.GET.get('date', '')
    if current_db:
        queryset = queryset.filter(database=current_db)
    if current_date:
        # For capacity, we are matching 'month' field which is a DateField
        queryset = queryset.filter(month=current_date)
        
    paginator = Paginator(queryset, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'capacity/list.html', {
        'page_obj': page_obj, 
        'is_admin': request.user.is_admin,
        'current_db': current_db,
        'current_date': current_date
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
    
    can_edit_always = request.user.is_admin or request.user.has_perm('api.change_capacityplanning')
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
    if not (request.user.is_admin or request.user.has_perm('api.delete_incidentregister')):
        messages.error(request, 'You do not have permission to delete incident records.')
        return redirect('incident_list')
    obj = get_object_or_404(IncidentRegister, pk=pk)
    obj.delete()
    messages.success(request, 'Incident record deleted successfully.')
    return redirect('incident_list')

@login_required
@require_POST
def replication_delete(request, pk):
    if not (request.user.is_admin or request.user.has_perm('api.delete_replicationmonitor')):
        messages.error(request, 'You do not have permission to delete replication records.')
        return redirect('replication_list')
    obj = get_object_or_404(ReplicationMonitor, pk=pk)
    obj.delete()
    messages.success(request, 'Replication record deleted successfully.')
    return redirect('replication_list')

@login_required
@require_POST
def backup_delete(request, pk):
    if not (request.user.is_admin or request.user.has_perm('api.delete_backuprecovery')):
        messages.error(request, 'You do not have permission to delete backup records.')
        return redirect('backup_list')
    obj = get_object_or_404(BackupRecovery, pk=pk)
    obj.delete()
    messages.success(request, 'Backup record deleted successfully.')
    return redirect('backup_list')

@login_required
@require_POST
def server_health_delete(request, pk):
    if not (request.user.is_admin or request.user.has_perm('api.delete_serverhealth')):
        messages.error(request, 'You do not have permission to delete server health records.')
        return redirect('server_health_list')
    obj = get_object_or_404(ServerHealth, pk=pk)
    obj.delete()
    messages.success(request, 'Server health record deleted successfully.')
    return redirect('server_health_list')

@login_required
@require_POST
def maintenance_delete(request, pk):
    if not (request.user.is_admin or request.user.has_perm('api.delete_maintenanceregister')):
        messages.error(request, 'You do not have permission to delete maintenance records.')
        return redirect('maintenance_list')
    obj = get_object_or_404(MaintenanceRegister, pk=pk)
    obj.delete()
    messages.success(request, 'Maintenance record deleted successfully.')
    return redirect('maintenance_list')

@login_required
@require_POST
def capacity_delete(request, pk):
    if not (request.user.is_admin or request.user.has_perm('api.delete_capacityplanning')):
        messages.error(request, 'You do not have permission to delete capacity planning records.')
        return redirect('capacity_list')
    obj = get_object_or_404(CapacityPlanning, pk=pk)
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
            database=data.get('database_instance', 'Unknown'),
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
