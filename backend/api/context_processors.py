from django.db.models import Q
from .models import IncidentStaging

def is_sysadmin_user(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or getattr(user, 'is_admin', False):
        return False
    is_sysadmin_group = user.groups.filter(
        Q(name__icontains='sysadmin') | Q(name__icontains='infra') | Q(name__icontains='system admin')
    ).exists()
    has_infra = (
        user.has_perm('api.view_infrastructuredashboard') or 
        user.has_perm('api.view_infrastructurecapacity') or
        user.has_perm('api.view_infrastructureincident') or
        user.has_perm('api.view_infrastructuremaintenance')
    )
    has_db = (
        user.has_perm('api.view_databasedashboard') or 
        user.has_perm('api.view_databasecapacity') or 
        user.has_perm('api.view_incidentregister') or
        user.has_perm('api.view_maintenanceregister') or
        user.has_perm('api.view_databaseincident') or
        user.has_perm('api.view_databasemaintenance')
    )
    return is_sysadmin_group or (has_infra and not has_db)

def pending_staging_count(request):
    if request.user and request.user.is_authenticated:
        is_sysadmin = is_sysadmin_user(request.user)
        if is_sysadmin:
            count = IncidentStaging.objects.filter(status='Awaiting Review').filter(
                Q(category='Infrastructure') | Q(database='Infrastructure') | Q(root_cause__icontains='Prometheus')
            ).count()
        else:
            count = IncidentStaging.objects.filter(status='Awaiting Review').count()
        return {
            'pending_staging_count': count,
            'is_sysadmin_user': is_sysadmin,
        }
    return {'pending_staging_count': 0, 'is_sysadmin_user': False}

