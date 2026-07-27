from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from api.models import IncidentRegister, IncidentChangeLog

class Command(BaseCommand):
    help = 'Auto-closes incidents that have been resolved for more than 24 hours'

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=1)
        resolved_incidents = IncidentRegister.objects.filter(
            status='Resolved',
            end__lte=cutoff
        )
        
        count = resolved_incidents.count()
        if count == 0:
            self.stdout.write("No incidents found that need auto-closing.")
            return

        for incident in resolved_incidents:
            incident.status = 'Closed'
            incident.save()
            
            IncidentChangeLog.objects.create(
                incident=incident,
                changed_by=None,  # None represents "System"
                action="Auto-closed by system after 24 hours of resolution",
                new_status="Closed"
            )
            self.stdout.write(f"Successfully auto-closed incident {incident.ticket}")

        self.stdout.write(self.style.SUCCESS(f"Successfully auto-closed {count} incidents."))
