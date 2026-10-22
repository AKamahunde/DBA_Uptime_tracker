from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta, date
from api.models import IncidentRegister, IncidentChangeLog, MaintenanceRegister, UgandaHoliday, ServerHealth
from etl.services.rest_client import sync_all_nodes_from_prometheus

class Command(BaseCommand):
    help = 'Executes periodic DBTRAC maintenance tasks: auto-closes resolved incidents, updates maintenance statuses, seeds holidays, and syncs Prometheus telemetry'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE(f"[{timezone.now()}] Starting DBTRAC system maintenance..."))
        
        # 1. Auto-close resolved incidents older than 24 hours
        cutoff = timezone.now() - timedelta(days=1)
        resolved_incidents = IncidentRegister.objects.filter(
            status='Resolved',
            end__lte=cutoff
        )
        closed_count = 0
        for incident in resolved_incidents:
            incident.status = 'Closed'
            incident.save()
            IncidentChangeLog.objects.create(
                incident=incident,
                changed_by=None,  # System
                action="Auto-closed by system after 24 hours of resolution",
                new_status="Closed"
            )
            closed_count += 1
        self.stdout.write(self.style.SUCCESS(f"Auto-closed {closed_count} resolved incidents."))

        # 2. Auto-update maintenance statuses
        now = timezone.now()
        started_count = MaintenanceRegister.objects.filter(
            status='Scheduled',
            start__lte=now,
            end__gt=now
        ).update(status='In Progress')

        completed_count = MaintenanceRegister.objects.filter(
            status__in=['Scheduled', 'In Progress'],
            end__lte=now
        ).update(status='Completed')
        self.stdout.write(self.style.SUCCESS(f"Updated maintenance windows: {started_count} In Progress, {completed_count} Completed."))

        # 3. Seed Uganda public holidays for current and next year if not already populated
        current_year = date.today().year
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
        seeded_count = 0
        for y in [current_year, current_year + 1]:
            for (month, day), name in fixed_holidays:
                try:
                    h_date = date(y, month, day)
                    _, created = UgandaHoliday.objects.get_or_create(
                        holiday_date=h_date,
                        defaults={'name': name}
                    )
                    if created:
                        seeded_count += 1
                except Exception:
                    pass
        self.stdout.write(self.style.SUCCESS(f"Seeded {seeded_count} new Uganda statutory holidays."))

        # 4. Sync Prometheus Node Metrics
        try:
            today_dt = timezone.now().date()
            self.stdout.write(self.style.NOTICE("Syncing Prometheus node metrics..."))
            synced, errors = sync_all_nodes_from_prometheus()
            self.stdout.write(self.style.SUCCESS(f"Prometheus sync completed: {len(synced)} nodes updated, {len(errors)} errors."))
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"Prometheus sync skipped/failed: {exc}"))

        self.stdout.write(self.style.SUCCESS(f"[{timezone.now()}] DBTRAC system maintenance finished successfully."))
