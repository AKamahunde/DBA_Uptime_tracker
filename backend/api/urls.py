from django.urls import path
from . import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('about/', views.about, name='about'),
    
    # Incident Register
    path('incidents/', views.incident_list, name='incident_list'),
    path('incidents/create/', views.incident_create, name='incident_create'),
    path('incidents/<int:pk>/edit/', views.incident_edit, name='incident_edit'),
    path('incidents/<int:pk>/delete/', views.incident_delete, name='incident_delete'),
    
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
    path('capacity/create/', views.capacity_create, name='capacity_create'),
    path('capacity/<int:pk>/edit/', views.capacity_edit, name='capacity_edit'),
    path('capacity/<int:pk>/delete/', views.capacity_delete, name='capacity_delete'),
]
