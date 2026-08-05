from .models import IncidentStaging

def pending_staging_count(request):
    if request.user and request.user.is_authenticated:
        count = IncidentStaging.objects.filter(status='Awaiting Review').count()
        return {'pending_staging_count': count}
    return {'pending_staging_count': 0}
