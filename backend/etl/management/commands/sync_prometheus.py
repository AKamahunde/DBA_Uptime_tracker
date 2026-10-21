from django.core.management.base import BaseCommand
from etl.services.rest_client import (
    sync_all_nodes_from_prometheus,
    scan_and_ingest_prometheus_outages,
    PROMETHEUS_URL
)


class Command(BaseCommand):
    help = "Queries Prometheus HTTP API (/api/v1/query) to sync OLKVM and Proxmox node metrics, scan for reboot gaps, and upsert ServerHealth."

    def add_arguments(self, parser):
        parser.add_argument(
            '--url',
            type=str,
            default=PROMETHEUS_URL,
            help=f"Base URL of the Prometheus server (default: {PROMETHEUS_URL})",
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
        prom_url = options['url']
        lookback = options.get('lookback', 36)
        skip_outages = options.get('no_outages', False)

        self.stdout.write(f"Syncing OLKVM and Proxmox node metrics from Prometheus ({prom_url})...")
        synced, errors = sync_all_nodes_from_prometheus(base_url=prom_url)

        for rec in synced:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[OK] {rec.node_name} ({rec.server}) -> Status: {rec.status} | "
                    f"Uptime: {rec.uptime_percent}% | CPU: {rec.cpu_percent}% | RAM: {rec.memory_percent}% | "
                    f"Disk1: {rec.disk_1_percent}% ({rec.disk_1_size_gb} GB) | "
                    f"Disk2: {rec.disk_2_percent}% ({rec.disk_2_size_gb} GB) | "
                    f"Disk3: {rec.disk_3_percent}% ({rec.disk_3_size_gb} GB)"
                )
            )

        if not skip_outages:
            self.stdout.write(f"Scanning Prometheus telemetry for reboot events / outage gaps (lookback={lookback}h)...")
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
                    self.stdout.write(self.style.SUCCESS('[OK] No new Prometheus telemetry gaps detected.'))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"[WARNING] Outage scan warning: {e}"))

        for err in errors:
            self.stderr.write(self.style.ERROR(f"[ERROR] {err}"))
