from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from api.models import IncidentRegister, SLAConfig
from api.sla_utils import (
    calculate_merged_operational_downtime,
    calculate_operational_downtime,
    calculate_service_desk_resolution_hours,
    merge_time_intervals,
)
from api.views import _get_dashboard_data, _get_guest_vm_fleet_summary


class SLAUtilsTests(TestCase):
    def _aware(self, year, month, day, hour, minute=0):
        tz = timezone.get_current_timezone()
        return timezone.make_aware(datetime(year, month, day, hour, minute, 0), tz)

    def test_mssql_uses_weekday_business_clock_not_infra(self):
        # Saturday 08:00-17:00 must be 0.00 hrs for MS SQL Server (not 24/7 Infrastructure)
        sat_start = self._aware(2026, 10, 17, 8, 0)
        sat_end = self._aware(2026, 10, 17, 17, 0)
        self.assertEqual(
            calculate_operational_downtime(sat_start, sat_end, 'MS SQL Server', holiday_dates_override=set()),
            Decimal('0.00'),
        )
        # Monday 08:00-12:00 must be 4.00 hrs
        mon_start = self._aware(2026, 10, 19, 8, 0)
        mon_end = self._aware(2026, 10, 19, 12, 0)
        self.assertEqual(
            calculate_operational_downtime(mon_start, mon_end, 'MS SQL Server', holiday_dates_override=set()),
            Decimal('4.00'),
        )

    def test_service_desk_resolution_hours_clock_q005(self):
        # Oracle weekday 06:00-16:00 is 10.00h on Oracle clock, but only 8.00h (08:00-16:00) on Service Desk clock
        start_dt = self._aware(2026, 10, 19, 6, 0)
        end_dt = self._aware(2026, 10, 19, 16, 0)
        self.assertEqual(
            calculate_operational_downtime(start_dt, end_dt, 'Oracle', holiday_dates_override=set()),
            Decimal('10.00'),
        )
        self.assertEqual(
            calculate_service_desk_resolution_hours(start_dt, end_dt, holiday_dates_override=set()),
            Decimal('8.00'),
        )

        # Weekend + Uganda Public Holiday exclusion (Fri 08:00 -> Tue 10:00 with Mon holiday = 9h + 2h = 11h)
        fri_start = self._aware(2026, 10, 9, 8, 0)
        tue_end = self._aware(2026, 10, 13, 10, 0)
        self.assertEqual(
            calculate_service_desk_resolution_hours(fri_start, tue_end, holiday_dates_override={date(2026, 10, 12)}),
            Decimal('11.00'),
        )

    def test_merge_time_intervals_prevents_double_counting_q006(self):
        # Host OLKVM outage 02:20-08:18 EAT + Oracle VM outage 02:28-08:18 EAT (contained)
        host_s = self._aware(2026, 10, 20, 2, 20)
        host_e = self._aware(2026, 10, 20, 8, 18)
        vm_s = self._aware(2026, 10, 20, 2, 28)
        vm_e = self._aware(2026, 10, 20, 8, 18)

        merged = merge_time_intervals([(vm_s, vm_e), (host_s, host_e)])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0], (host_s, host_e))
        self.assertEqual(
            calculate_merged_operational_downtime([(vm_s, vm_e), (host_s, host_e)], database='Infrastructure'),
            5.97,
        )

        # Partial overlap + disjoint interval: [10:00-12:00], [11:00-13:00], [15:00-16:00] => 3h + 1h = 4.00h
        intervals = [
            (self._aware(2026, 10, 20, 10, 0), self._aware(2026, 10, 20, 12, 0)),
            (self._aware(2026, 10, 20, 11, 0), self._aware(2026, 10, 20, 13, 0)),
            (self._aware(2026, 10, 20, 15, 0), self._aware(2026, 10, 20, 16, 0)),
        ]
        self.assertEqual(calculate_merged_operational_downtime(intervals, database='Infrastructure'), 4.00)


class GovernanceSLARulesTests(TestCase):
    def _aware(self, year, month, day, hour, minute=0):
        tz = timezone.get_current_timezone()
        return timezone.make_aware(datetime(year, month, day, hour, minute, 0), tz)

    def test_flat_9995_sla_target_across_all_databases_q003(self):
        dashboard_data, _ = _get_dashboard_data(date(2026, 12, 1))
        targets_by_db = {item['database']: item['target_sla_val'] for item in dashboard_data}
        for db in ('Oracle', 'MySQL', 'MS SQL Server', 'PostgreSQL'):
            self.assertEqual(targets_by_db[db], 99.95)
            cfg = SLAConfig.objects.get(database=db, month=date(2026, 12, 1))
            self.assertEqual(cfg.sla_percent, Decimal('99.95'))

    def test_oracle_critical_offhours_not_flagged_as_service_desk_breach_q005(self):
        # Mon 06:00-16:00 EAT: 10.00h Oracle uptime impact, 8.00h Service Desk hours (<= 9h Critical SLA)
        IncidentRegister.objects.create(
            ticket='INC-TEST-Q005-OK',
            date=date(2026, 12, 7),
            start=self._aware(2026, 12, 7, 6, 0),
            end=self._aware(2026, 12, 7, 16, 0),
            planned=False,
            severity='Critical',
            category='Database',
            database='Oracle',
            system='nmsinfra',
            status='Closed',
        )
        dashboard_data, _ = _get_dashboard_data(date(2026, 12, 1))
        oracle_row = next(r for r in dashboard_data if r['database'] == 'Oracle')
        self.assertEqual(oracle_row['sla_breach_count'], 0)

        # Mon 08:00 -> Tue 10:00 EAT: 11.00h Service Desk hours (> 9h Critical SLA) => 1 breach
        IncidentRegister.objects.create(
            ticket='INC-TEST-Q005-BREACH',
            date=date(2026, 12, 14),
            start=self._aware(2026, 12, 14, 8, 0),
            end=self._aware(2026, 12, 15, 10, 0),
            planned=False,
            severity='Critical',
            category='Database',
            database='Oracle',
            system='nmsinfra',
            status='Closed',
        )
        dashboard_data, _ = _get_dashboard_data(date(2026, 12, 1))
        oracle_row = next(r for r in dashboard_data if r['database'] == 'Oracle')
        self.assertEqual(oracle_row['sla_breach_count'], 1)

    def test_guest_vm_overlapping_host_and_vm_outages_merged_q006(self):
        IncidentRegister.objects.create(
            ticket='INC-TEST-HOST',
            date=date(2026, 10, 20),
            start=self._aware(2026, 10, 20, 2, 20),
            end=self._aware(2026, 10, 20, 8, 18),
            planned=False,
            severity='Medium',
            category='Infrastructure',
            node_name='OLKVM',
            database='Infrastructure',
            system='OLKVM Host Node (Oracle DB & EBS)',
            status='Closed',
        )
        IncidentRegister.objects.create(
            ticket='INC-TEST-VM',
            date=date(2026, 10, 20),
            start=self._aware(2026, 10, 20, 2, 28),
            end=self._aware(2026, 10, 20, 8, 18),
            planned=False,
            severity='High',
            category='Database',
            node_name='',
            database='Oracle',
            system='nmsinfra and nmsebs',
            status='Closed',
        )
        # Single-host Proxmox incident on pvenode1 must only affect tomcatvm, not databasevm on pvenode3
        IncidentRegister.objects.create(
            ticket='INC-TEST-PVE1',
            date=date(2026, 10, 21),
            start=self._aware(2026, 10, 21, 10, 0),
            end=self._aware(2026, 10, 21, 12, 0),
            planned=False,
            severity='High',
            category='Infrastructure',
            node_name='pvenode1',
            database='Infrastructure',
            system='pvenode1',
            status='Closed',
        )

        summary = _get_guest_vm_fleet_summary(target_month_dt=date(2026, 10, 1))
        by_key = {vm['vm_key']: vm for vm in summary['vms']}
        self.assertEqual(by_key['nmsinfra']['unplanned_down_hrs'], 5.97)
        self.assertEqual(by_key['nmsebs']['unplanned_down_hrs'], 5.97)
        self.assertEqual(by_key['tomcatvm']['unplanned_down_hrs'], 2.00)
        self.assertEqual(by_key['databasevm']['unplanned_down_hrs'], 0.00)
