from django.urls import path
from . import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('dashboard/print/', views.dashboard_print, name='dashboard_print'),
    path('dashboard/excel/', views.dashboard_excel, name='dashboard_excel'),
    path('login/', views.CustomLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('password-change/', views.password_change_required, name='password_change_required'),
    path('about/', views.about, name='about'),
    
    # Incident Register
    path('incidents/', views.incident_list, name='incident_list'),
    path('incidents/create/', views.incident_create, name='incident_create'),
    path('incidents/<int:pk>/edit/', views.incident_edit, name='incident_edit'),
    path('incidents/<int:pk>/delete/', views.incident_delete, name='incident_delete'),
    path('incidents/staging/', views.incident_staging_list, name='incident_staging_list'),
    path('incidents/staging/<int:pk>/promote-incident/', views.staging_promote_incident, name='staging_promote_incident'),
    path('incidents/staging/<int:pk>/promote-maintenance/', views.staging_promote_maintenance, name='staging_promote_maintenance'),
    path('incidents/staging/<int:pk>/delete/', views.staging_delete, name='staging_delete'),
    
    # Replication Monitor
    path('replication/', views.replication_list, name='replication_list'),
    path('replication/create/', views.replication_create, name='replication_create'),
    path('replication/<int:pk>/edit/', views.replication_edit, name='replication_edit'),
    path('replication/<int:pk>/delete/', views.replication_delete, name='replication_delete'),
    
    # Backup & Recovery
    path('backups/', views.backup_list, name='backup_list'),
    path('backups/create/', views.backup_create, name='backup_create'),
    path('backups/<int:pk>/edit/', views.backup_edit, name='backup_edit'),
    path('backups/<int:pk>/delete/', views.backup_delete, name='backup_delete'),
    
    # Server Health
    path('server-health/', views.server_health_list, name='server_health_list'),
    path('server-health/sync-prometheus/', views.server_health_sync_prometheus, name='server_health_sync_prometheus'),
    path('server-health/create/', views.server_health_create, name='server_health_create'),
    path('server-health/<int:pk>/edit/', views.server_health_edit, name='server_health_edit'),
    path('server-health/<int:pk>/delete/', views.server_health_delete, name='server_health_delete'),
    
    # Maintenance Register
    path('maintenance/', views.maintenance_list, name='maintenance_list'),
    path('maintenance/create/', views.maintenance_create, name='maintenance_create'),
    path('maintenance/<int:pk>/edit/', views.maintenance_edit, name='maintenance_edit'),
    path('maintenance/<int:pk>/delete/', views.maintenance_delete, name='maintenance_delete'),
    
    # Capacity Planning
    path('capacity/', views.capacity_list, name='capacity_list'),
    path('capacity/print/', views.capacity_print, name='capacity_print'),
    path('capacity/excel/', views.capacity_excel, name='capacity_excel'),
    path('capacity/create/', views.capacity_create, name='capacity_create'),
    path('capacity/<int:pk>/edit/', views.capacity_edit, name='capacity_edit'),
    path('capacity/<int:pk>/delete/', views.capacity_delete, name='capacity_delete'),
    
    # SLA Configuration
    path('sla-config/', views.sla_config_list, name='sla_config_list'),
    path('sla-config/create/', views.sla_config_create, name='sla_config_create'),
    path('sla-config/<int:pk>/edit/', views.sla_config_edit, name='sla_config_edit'),
    path('sla-config/<int:pk>/delete/', views.sla_config_delete, name='sla_config_delete'),
    
    # Public Holidays
    path('holidays/', views.holiday_list, name='holiday_list'),
    path('holidays/create/', views.holiday_create, name='holiday_create'),
    path('holidays/<str:pk>/edit/', views.holiday_edit, name='holiday_edit'),
    path('holidays/<str:pk>/delete/', views.holiday_delete, name='holiday_delete'),
    
    # API endpoints for remote ingestion
    path('api/replication/ingest/', views.replication_ingest, name='replication_ingest'),
    path('api/backup/ingest/', views.backup_ingest, name='backup_ingest'),
    path('api/health/ingest/', views.server_health_ingest, name='server_health_ingest'),
    path('api/capacity/ingest/', views.capacity_ingest, name='capacity_ingest'),
    path('api/incident/ingest/', views.incident_ingest, name='incident_ingest'),
]
