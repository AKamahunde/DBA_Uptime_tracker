from django.core.management.base import BaseCommand
from etl.services.rest_client import sync_all_nodes_from_prometheus, PROMETHEUS_URL


class Command(BaseCommand):
    help = "Queries Prometheus HTTP API (/api/v1/query) to sync OLKVM and Proxmox node metrics and 3 disk types into ServerHealth."

    def add_arguments(self, parser):
        parser.add_argument(
            '--url',
            type=str,
            default=PROMETHEUS_URL,
            help=f"Base URL of the Prometheus server (default: {PROMETHEUS_URL})",
        )

    def handle(self, *args, **options):
        prom_url = options['url']
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

        for err in errors:
            self.stderr.write(self.style.ERROR(f"[ERROR] {err}"))
