from django.contrib import admin
from .models import IncidentRegister, ReplicationMonitor, BackupRecovery, ServerHealth, MaintenanceRegister, CapacityPlanning

@admin.register(IncidentRegister)
class IncidentRegisterAdmin(admin.ModelAdmin):
    list_display = ['ticket', 'date', 'severity', 'status', 'database']
    list_filter = ['severity', 'status', 'database', 'planned']
    search_fields = ['ticket', 'system', 'resolved_by']

@admin.register(ReplicationMonitor)
class ReplicationMonitorAdmin(admin.ModelAdmin):
    list_display = ['date', 'master', 'replica', 'status', 'database']
    list_filter = ['status', 'database', 'severity']
    search_fields = ['master', 'replica']

@admin.register(BackupRecovery)
class BackupRecoveryAdmin(admin.ModelAdmin):
    list_display = ['date', 'record_type', 'database', 'outcome', 'verified']
    list_filter = ['record_type', 'database', 'outcome', 'verified']
    search_fields = ['database', 'database_instance']

@admin.register(ServerHealth)
class ServerHealthAdmin(admin.ModelAdmin):
    list_display = ['date', 'server', 'cpu_percent', 'memory_percent', 'status', 'database']
    list_filter = ['status', 'database']
    search_fields = ['server', 'database']

@admin.register(MaintenanceRegister)
class MaintenanceRegisterAdmin(admin.ModelAdmin):
    list_display = ['date', 'change_id', 'risk', 'success', 'database']
    list_filter = ['risk', 'success', 'database']
    search_fields = ['change_id', 'activity', 'approved_by']

@admin.register(CapacityPlanning)
class CapacityPlanningAdmin(admin.ModelAdmin):
    list_display = ['month', 'database', 'database_size_gb', 'disk_used_percent']
    list_filter = ['database']
    search_fields = ['database', 'largest_table']

