from django.core.management.base import BaseCommand
from etl.services.rest_client import (
    sync_all_nodes_from_prometheus,
    scan_and_ingest_prometheus_outages,
    PROMETHEUS_URL
)


class Command(BaseCommand):
    help = 'Pulls latest node and 3-disk capacity telemetry from Prometheus, scans for reboot/scrape outage gaps, and upserts ServerHealth records.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--url',
            type=str,
            default=None,
            help=f'Prometheus server URL (default: {PROMETHEUS_URL})',
        )
        parser.add_argument(
            '--lookback',
            type=int,
            default=36,
            help='Lookback window in hours for outage/reboot gap detection (default: 36)',
        )
        parser.add_argument(
            '--no-outages',
            action='store_true',
            help='Skip automatic Prometheus scrape gap outage detection',
        )

    def handle(self, *args, **options):
        prom_url = options.get('url')
        lookback = options.get('lookback', 36)
        skip_outages = options.get('no_outages', False)

        self.stdout.write(self.style.NOTICE(f'Syncing node telemetry from Prometheus ({prom_url or PROMETHEUS_URL})...'))
        synced, errors = sync_all_nodes_from_prometheus(prometheus_url=prom_url)

        for record in synced:
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Synced {record.node_name} — {record.server}: Uptime={record.uptime_percent}%, CPU={record.cpu_percent}%, RAM={record.memory_percent}%"
                )
            )

        if not skip_outages:
            self.stdout.write(self.style.NOTICE(f'Scanning Prometheus telemetry for reboot events / outage gaps (lookback={lookback}h)...'))
            try:
                new_incidents = scan_and_ingest_prometheus_outages(prometheus_url=prom_url, lookback_hours=lookback)
                if new_incidents:
                    for inc in new_incidents:
                        self.stdout.write(
                            self.style.WARNING(
                                f"⚡ Auto-logged 24/7 Outage Incident: {inc.ticket} on {inc.node_name or inc.system} "
                                f"({inc.duration_hrs}h, Status: {inc.status})"
                            )
                        )
                else:
                    self.stdout.write(self.style.SUCCESS('✓ No new Prometheus telemetry gaps detected.'))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"⚠ Outage scan warning: {e}"))

        if errors:
            for err in errors:
                self.stdout.write(self.style.WARNING(f"⚠ Warning: {err}"))
            self.stdout.write(self.style.WARNING(f"Completed with {len(synced)} synced and {len(errors)} warnings."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Successfully synced all {len(synced)} nodes into today's records."))
