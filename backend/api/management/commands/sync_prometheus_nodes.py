from django.core.management.base import BaseCommand
from etl.services.rest_client import sync_all_nodes_from_prometheus, PROMETHEUS_URL


class Command(BaseCommand):
    help = 'Pulls latest node and 3-disk capacity telemetry from Prometheus and upserts today\'s ServerHealth records.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--url',
            type=str,
            default=None,
            help=f'Prometheus server URL (default: {PROMETHEUS_URL})',
        )

    def handle(self, *args, **options):
        prom_url = options.get('url')
        self.stdout.write(self.style.NOTICE(f'Syncing node telemetry from Prometheus ({prom_url or PROMETHEUS_URL})...'))
        synced, errors = sync_all_nodes_from_prometheus(prometheus_url=prom_url)

        for record in synced:
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Synced {record.node_name} — {record.server}: Uptime={record.uptime_percent}%, CPU={record.cpu_percent}%, RAM={record.memory_percent}%"
                )
            )

        if errors:
            for err in errors:
                self.stdout.write(self.style.WARNING(f"⚠ Warning: {err}"))
            self.stdout.write(self.style.WARNING(f"Completed with {len(synced)} synced and {len(errors)} warnings."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Successfully synced all {len(synced)} nodes into today's records."))
