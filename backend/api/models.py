from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


def format_human_duration(hrs_val):
    """Format duration in hours into clean human-readable representation:
       e.g., 0.01h -> '< 1 min', 0.02h -> '1 min', 0.30h -> '18 mins', 2.30h -> '2h 18m', 1.00h -> '1h'
    """
    if hrs_val is None:
        return "0 mins"
    try:
        val = float(hrs_val)
    except (ValueError, TypeError):
        return "0 mins"

    if val <= 0:
        return "0 mins"

    secs = int(round(val * 3600))
    if secs < 60:
        return "< 1 min"
    if secs < 3600:
        mins = int(round(secs / 60.0))
        return "1 min" if mins <= 1 else f"{mins} mins"

    h = int(secs // 3600)
    m = int(round((secs % 3600) / 60.0))
    if m == 60:
        h += 1
        m = 0
    if m == 0:
        return f"{h}h"
    return f"{h}h {m}m"


class IncidentRegister(models.Model):
    ticket = models.CharField(max_length=50)
    date = models.DateField()
    start = models.DateTimeField(null=True, blank=True)
    end = models.DateTimeField(null=True, blank=True)
    duration_hrs = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    planned = models.BooleanField(default=False)
    severity = models.CharField(max_length=20, choices=[
        ('Low', 'Low'),
        ('Medium', 'Medium'),
        ('High', 'High'),
        ('Critical', 'Critical')
    ])
    system = models.CharField(max_length=100)
    root_cause = models.TextField(blank=True)
    business_impact = models.TextField(blank=True)
    resolved_by = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=[
        ('Open', 'Open'),
        ('In Progress', 'In Progress'),
        ('Resolved', 'Resolved'),
        ('Closed', 'Closed')
    ])
    category = models.CharField(max_length=20, choices=[
        ('Database', 'Database'),
        ('Infrastructure', 'Infrastructure'),
    ], default='Database')
    node_name = models.CharField(max_length=100, blank=True, default='')
    database = models.CharField(max_length=50, choices=[
        ('MySQL', 'MySQL'),
        ('Oracle', 'Oracle'),
        ('MS SQL Server', 'MS SQL Server'),
        ('Infrastructure', 'Infrastructure'),
    ], default='MySQL', blank=True)
    assigned_to = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'incident_register'
        verbose_name = 'Incident Register'
        verbose_name_plural = 'Incident Registers'
        default_permissions = ('add', 'view')

    def save(self, *args, **kwargs):
        if not self.start:
            self.start = timezone.now()

        if self.status in ['Resolved', 'Closed'] and not self.end:
            self.end = timezone.now()

        if self.category == 'Infrastructure':
            # Infrastructure host nodes run 24/7 (24 hours/day, 7 days/week)
            if self.end and self.start and self.end > self.start:
                secs = (self.end - self.start).total_seconds()
                self.duration_hrs = round(secs / 3600.0, 2)
            elif self.start and self.status not in ['Resolved', 'Closed']:
                secs = (timezone.now() - self.start).total_seconds()
                self.duration_hrs = round(max(0.0, secs) / 3600.0, 2)
            else:
                self.duration_hrs = 0
            if not self.database or self.database == 'MySQL':
                self.database = 'Infrastructure'
        else:
            from api.sla_utils import calculate_operational_downtime
            if self.end and self.start:
                self.duration_hrs = calculate_operational_downtime(self.start, self.end, self.database)
            elif self.start and self.status not in ['Resolved', 'Closed']:
                self.duration_hrs = calculate_operational_downtime(self.start, timezone.now(), self.database)
            else:
                self.duration_hrs = 0
            
        # Capture original ticket BEFORE saving so we can locate the existing
        # maintenance record even when the ticket ID is being renamed.
        original_ticket = self.ticket  # fallback for new records
        if self.pk:
            try:
                original_ticket = IncidentRegister.objects.filter(pk=self.pk).values_list('ticket', flat=True).first() or self.ticket
            except Exception:
                pass

        super().save(*args, **kwargs)

        # Sync to Maintenance Register
        from api.models import MaintenanceRegister

        if self.planned:
            maint_date = self.start.date() if self.start else self.date
            maint_start = self.start if self.start else timezone.now()
            maint_end = self.end if self.end else maint_start

            MaintenanceRegister.objects.update_or_create(
                change_id=original_ticket,   # look up by ORIGINAL ticket
                defaults={
                    'change_id': self.ticket,    # update to NEW ticket name
                    'database': self.database,
                    'system': self.system,        # Sync system name (e.g. nmsinfra (DB))
                    'date': maint_date,
                    'activity': f"Planned Incident Outage: {self.root_cause or 'Linked from ticket ' + self.ticket}",
                    'start': maint_start,
                    'end': maint_end,
                    'risk': 'Medium',
                    'rollback_plan': 'Awaiting DBA input.',
                    'approved_by': '',  # Leave blank so a DBA can update it later
                    'success': True if self.status in ['Resolved', 'Closed'] else False,
                    'created_by': self.created_by
                }
            )
        else:
            # Incident marked unplanned — remove any linked maintenance record
            # (check both current and original ticket name in case it was renamed)
            MaintenanceRegister.objects.filter(change_id__in=[self.ticket, original_ticket]).delete()

    def __str__(self):
        return f"{self.ticket} - {self.date}"

    @property
    def elapsed_duration_hrs(self):
        """Total wall-clock elapsed duration between start and end (24/7)."""
        if self.start and self.end and self.end > self.start:
            secs = (self.end - self.start).total_seconds()
            return round(secs / 3600.0, 2)
        return float(self.duration_hrs or 0.0)

    @property
    def elapsed_duration_mins(self):
        """Total wall-clock elapsed duration in minutes."""
        if self.start and self.end and self.end > self.start:
            secs = (self.end - self.start).total_seconds()
            return round(secs / 60.0, 1)
        return round(float(self.duration_hrs or 0.0) * 60.0, 1)

    @property
    def has_off_hours_difference(self):
        """Returns True if the elapsed wall-clock downtime differs noticeably from SLA business hours downtime."""
        try:
            return abs(float(self.elapsed_duration_hrs) - float(self.duration_hrs or 0.0)) >= 0.05
        except Exception:
            return False

    @property
    def off_hours_duration_hrs(self):
        """Duration of the outage that fell outside the SLA business operating window."""
        try:
            diff = float(self.elapsed_duration_hrs) - float(self.duration_hrs or 0.0)
            return round(max(0.0, diff), 2)
        except Exception:
            return 0.0

    @property
    def human_duration(self):
        """Clean human-readable SLA outage duration (e.g., '18 mins', '2h 18m', '< 1 min')."""
        return format_human_duration(self.duration_hrs)

    @property
    def human_elapsed_duration(self):
        """Clean human-readable total wall-clock elapsed duration (e.g., '5h 58m', '18 mins')."""
        return format_human_duration(self.elapsed_duration_hrs)

class IncidentChangeLog(models.Model):
    incident = models.ForeignKey(IncidentRegister, on_delete=models.CASCADE, related_name='change_logs')
    changed_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=50)
    old_status = models.CharField(max_length=50, blank=True)
    new_status = models.CharField(max_length=50, blank=True)
    assigned_to = models.CharField(max_length=100, blank=True)
    comment = models.TextField(blank=True)

    class Meta:
        db_table = 'incident_change_log'
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.incident.ticket} - {self.action} on {self.changed_at}"

class ReplicationMonitor(models.Model):
    date = models.DateField()
    master = models.CharField(max_length=100)
    replica = models.CharField(max_length=100)
    io_thread = models.CharField(max_length=20, choices=[
        ('Yes', 'Yes'),
        ('No', 'No'),
        ('Connecting', 'Connecting')
    ])
    sql_thread = models.CharField(max_length=20, choices=[
        ('Yes', 'Yes'),
        ('No', 'No'),
        ('Connecting', 'Connecting')
    ])
    seconds_behind = models.IntegerField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=[
        ('Healthy', 'Healthy'),
        ('Failed', 'Failed'),
    ])
    database = models.CharField(max_length=50, choices=[
        ('MySQL', 'MySQL'),
        ('Oracle', 'Oracle'),
        ('MS SQL Server', 'MS SQL Server'),
    ])
    severity = models.CharField(max_length=20, choices=[
        ('Low', 'Low'),
        ('Medium', 'Medium'),
        ('High', 'High'),
        ('Critical', 'Critical')
    ])
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if (self.io_thread == 'Yes' and 
            self.sql_thread == 'Yes' and 
            self.seconds_behind is not None and 
            self.seconds_behind <= 120):
            self.status = 'Healthy'
        else:
            self.status = 'Failed'
        super().save(*args, **kwargs)

    class Meta:
        db_table = 'replication_monitor'
        verbose_name = 'Replication Monitor'
        verbose_name_plural = 'Replication Monitors'
        default_permissions = ('add', 'view')

    def __str__(self):
        return f"{self.master} -> {self.replica} - {self.date}"

class BackupRecovery(models.Model):
    record_type = models.CharField(max_length=20, choices=[
        ('Backup', 'Backup'),
        ('Recovery', 'Recovery')
    ])
    database = models.CharField(max_length=50, choices=[
        ('MySQL', 'MySQL'),
        ('Oracle', 'Oracle'),
        ('MS SQL Server', 'MS SQL Server'),
    ])
    date = models.DateField()
    database_instance = models.CharField(max_length=100)
    backup_type = models.CharField(max_length=20, choices=[
        ('Full', 'Full'),
        ('Incremental', 'Incremental'),
        ('Differential', 'Differential')
    ])
    backup_used = models.CharField(max_length=255)
    start = models.DateTimeField()
    end = models.DateTimeField()
    duration_mins = models.DecimalField(max_digits=10, decimal_places=2)
    outcome = models.CharField(max_length=20, choices=[
        ('Success', 'Success'),
        ('Failed', 'Failed'),
        ('Partial', 'Partial')
    ])
    failure_reason = models.TextField(blank=True)
    location = models.CharField(max_length=200)
    verified = models.BooleanField(default=False)
    restore_tested = models.BooleanField(default=False)
    compression = models.BooleanField(default=False)
    tested_by = models.CharField(max_length=100, blank=True)
    comments = models.TextField(blank=True)
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'backup_recovery'
        verbose_name = 'Backup & Recovery'
        verbose_name_plural = 'Backup & Recovery'
        default_permissions = ('add', 'view')

    def __str__(self):
        return f"{self.record_type} - {self.database} - {self.date}"

class ServerHealth(models.Model):
    date = models.DateField()
    node_name = models.CharField(max_length=50, choices=[
        ('OLKVM', 'OLKVM Node'),
        ('Proxmox', 'Proxmox Node'),
    ], default='Proxmox', blank=True)
    hosted_systems = models.CharField(max_length=200, blank=True, default='')
    server = models.CharField(max_length=100)
    entered_by = models.CharField(max_length=20, choices=[
        ('User', 'User'),
        ('Script', 'Script'),
        ('Prometheus', 'Prometheus')
    ], default='User')
    uptime_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=100.00,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    uptime_days = models.DecimalField(max_digits=8, decimal_places=2, default=0.00)
    logical_cpus = models.IntegerField(default=0)
    cpu_percent = models.DecimalField(
        max_digits=5, 
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    memory_percent = models.DecimalField(
        max_digits=5, 
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    memory_total_gb = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    memory_used_gb = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    disk_percent = models.DecimalField(
        max_digits=5, 
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    storage_growth_gb = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    storage_growth_pct = models.DecimalField(max_digits=7, decimal_places=3, default=0.000)
    # 3 Disk Types (Categorized by Size & Type)
    disk_1_type = models.CharField(max_length=100, blank=True, default='Disk 1 (SSD / OS)')
    disk_1_size_gb = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    disk_1_used_gb = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    disk_1_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    disk_2_type = models.CharField(max_length=100, blank=True, default='Disk 2 (SAS / Data)')
    disk_2_size_gb = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    disk_2_used_gb = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    disk_2_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    disk_3_type = models.CharField(max_length=100, blank=True, default='Disk 3 (SAN / Backup)')
    disk_3_size_gb = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    disk_3_used_gb = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    disk_3_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    tablespace_percent = models.DecimalField(
        max_digits=5, 
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    connections = models.IntegerField(default=0)
    slow_queries = models.IntegerField(default=0)
    threads = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=[
        ('Healthy', 'Healthy'),
        ('Warning', 'Warning'),
        ('Critical', 'Critical')
    ])
    database = models.CharField(max_length=50, choices=[
        ('MySQL', 'MySQL'),
        ('Oracle', 'Oracle'),
        ('MS SQL Server', 'MS SQL Server'),
    ])
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def disk_1_free_gb(self):
        return round(max(0.0, float(self.disk_1_size_gb or 0) - float(self.disk_1_used_gb or 0)), 2)

    @property
    def disk_2_free_gb(self):
        return round(max(0.0, float(self.disk_2_size_gb or 0) - float(self.disk_2_used_gb or 0)), 2)

    @property
    def disk_3_free_gb(self):
        return round(max(0.0, float(self.disk_3_size_gb or 0) - float(self.disk_3_used_gb or 0)), 2)

    @property
    def display_server(self):
        s = self.server or ''
        if s.startswith('nmsinfra'):
            return s.replace('nmsinfra', 'infra', 1)
        if s.startswith('nmsebs'):
            return s.replace('nmsebs', 'ebs', 1)
        return s

    @property
    def display_node_label(self):
        # For OLKVM, maintain just "OLKVM" as node label (workload/server details are shown below the label)
        srv_lower = (self.server or '').lower()
        if (self.node_name or '').upper() == 'OLKVM' or self.database == 'Oracle' or any(k in srv_lower for k in ['olkvm', 'kvm', 'infra', 'ebs']):
            return 'OLKVM'
        srv = self.display_server
        if self.database == 'MS SQL Server':
            srv = 'Pentana'
        if self.node_name:
            return f"{self.node_name} — {srv}"
        return srv

    @property
    def clean_hosted_systems(self):
        import re
        txt = self.hosted_systems or ''
        srv_lower = (self.server or '').lower()
        if not txt or txt == self.database:
            if 'infra' in srv_lower:
                return 'Oracle DB (nmsinfra)'
            elif 'ebs' in srv_lower:
                return 'Oracle EBS App (nmsebs)'
            elif 'pvenode1' in srv_lower:
                return 'tomcatvm & Internal Systems'
            elif 'pvenode2' in srv_lower:
                return 'jumphostvm & monitoringvm'
            elif 'pvenode3' in srv_lower:
                return 'databasevm (MySQL / Pentana)'
            elif (self.node_name or '').upper() == 'OLKVM':
                return 'Oracle DB (nmsinfra) & Oracle EBS App (nmsebs)'
            elif self.database:
                return self.database
        # Strip any IP addresses
        txt = re.sub(r'\s*-\s*\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', '', txt)
        txt = re.sub(r'\s*\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '', txt)
        # Normalize to nmsinfra and nmsebs
        if 'infra' in txt and 'nmsinfra' not in txt:
            txt = txt.replace('infra', 'nmsinfra')
        if 'ebs' in txt and 'nmsebs' not in txt:
            txt = txt.replace('ebs', 'nmsebs')
        txt = re.sub(r'\(\s*\)', '', txt).strip(' -()')
        return txt or self.database

    @property
    def total_disk_capacity_gb(self):
        return round(float(self.disk_1_size_gb or 0) + float(self.disk_2_size_gb or 0) + float(self.disk_3_size_gb or 0), 2)

    @property
    def total_disk_used_gb(self):
        return round(float(self.disk_1_used_gb or 0) + float(self.disk_2_used_gb or 0) + float(self.disk_3_used_gb or 0), 2)

    @property
    def total_disk_free_gb(self):
        return round(max(0.0, self.total_disk_capacity_gb - self.total_disk_used_gb), 2)

    def save(self, *args, **kwargs):
        srv_lower = (self.server or '').lower()
        if not self.node_name or (self.node_name == 'Proxmox' and (self.database == 'Oracle' or any(k in srv_lower for k in ['olkvm', 'kvm', 'nmsinfra', 'nmsebs', 'infra', 'ebs']))):
            if self.database == 'Oracle' or any(k in srv_lower for k in ['olkvm', 'kvm', 'nmsinfra', 'nmsebs', 'infra', 'ebs']):
                self.node_name = 'OLKVM'
            else:
                self.node_name = 'Proxmox'

        if not self.hosted_systems:
            if 'nmsinfra' in srv_lower or 'infra' in srv_lower:
                self.hosted_systems = 'Oracle DB (nmsinfra)'
            elif 'nmsebs' in srv_lower or 'ebs' in srv_lower:
                self.hosted_systems = 'Oracle EBS App (nmsebs)'
            elif 'pvenode1' in srv_lower:
                self.hosted_systems = 'tomcatvm & Internal Systems'
            elif 'pvenode2' in srv_lower:
                self.hosted_systems = 'jumphostvm & monitoringvm'
            elif 'pvenode3' in srv_lower:
                self.hosted_systems = 'databasevm (MySQL / Pentana)'
            elif self.node_name == 'OLKVM':
                self.hosted_systems = 'Oracle DB (nmsinfra) & Oracle EBS App (nmsebs)'
            else:
                self.hosted_systems = 'MySQL (nickledbnew), MS SQL (Pentana) & Internal Systems'

        # If 3-disk percentages are provided and overall disk_percent is 0, set disk_percent to highest disk utilization
        max_disk_pct = max(float(self.disk_1_percent or 0), float(self.disk_2_percent or 0), float(self.disk_3_percent or 0))
        if max_disk_pct > 0 and float(self.disk_percent or 0) == 0:
            self.disk_percent = round(max_disk_pct, 2)

        super().save(*args, **kwargs)

    class Meta:
        db_table = 'server_health'
        verbose_name = 'Server Health'
        verbose_name_plural = 'Server Health'
        default_permissions = ('add', 'view')
        permissions = [
            ('view_databasedashboard', 'Can view Database Operations Dashboard'),
            ('view_infrastructuredashboard', 'Can view Node Infrastructure Dashboard'),
        ]

    def __str__(self):
        return f"{self.node_name} ({self.server}) - {self.date}"

class MaintenanceRegister(models.Model):
    date = models.DateField()
    change_id = models.CharField(max_length=50)
    system = models.CharField(max_length=100, blank=True, default='')
    activity = models.TextField()
    start = models.DateTimeField()
    end = models.DateTimeField()
    duration_hrs = models.DecimalField(max_digits=10, decimal_places=2)
    risk = models.CharField(max_length=20, choices=[
        ('Low', 'Low'),
        ('Medium', 'Medium'),
        ('High', 'High')
    ])
    rollback_plan = models.TextField()
    approved_by = models.CharField(max_length=100, blank=True)
    success = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=[
        ('Scheduled', 'Scheduled'),
        ('In Progress', 'In Progress'),
        ('Completed', 'Completed'),
        ('Failed', 'Failed')
    ], default='Scheduled')
    database = models.CharField(max_length=50, choices=[
        ('MySQL', 'MySQL'),
        ('Oracle', 'Oracle'),
        ('MS SQL Server', 'MS SQL Server'),
    ])
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self.start:
            # Auto-generate change ID based on start date: CHG + DD + MM (e.g. CHG2108 for 21 Aug)
            self.change_id = f"CHG{self.start.strftime('%d%m')}"

        if self.start and self.end:
            delta = self.end - self.start
            # Convert timedelta to hours
            self.duration_hrs = round(delta.total_seconds() / 3600, 2)
        elif not self.duration_hrs:
            self.duration_hrs = 0
            
        # Sync status and success flags for backward compatibility
        if self.status == 'Completed':
            self.success = True
        elif self.status == 'Failed':
            self.success = False
        elif self.success:
            self.status = 'Completed'
            
        super().save(*args, **kwargs)

    class Meta:
        db_table = 'maintenance_register'
        verbose_name = 'Maintenance Register'
        verbose_name_plural = 'Maintenance Registers'
        default_permissions = ('add', 'view')

    def __str__(self):
        return f"{self.change_id} - {self.date}"

class CapacityPlanning(models.Model):
    month = models.DateField()
    database_size_gb = models.DecimalField(max_digits=15, decimal_places=2)
    available_capacity_gb = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    disk_used_percent = models.DecimalField(
        max_digits=5, 
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    tablespace_used_percent = models.DecimalField(
        max_digits=5, 
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    largest_table = models.CharField(max_length=100)
    growth_gb = models.DecimalField(max_digits=15, decimal_places=2)
    database = models.CharField(max_length=50, choices=[
        ('MySQL', 'MySQL'),
        ('Oracle', 'Oracle'),
        ('MS SQL Server', 'MS SQL Server'),
    ])
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def growth_percent(self):
        if self.database_size_gb and self.database_size_gb > 0:
            return round((self.growth_gb / self.database_size_gb) * 100, 2)
        return 0.00

    @property
    def disk_capacity_gb(self):
        if self.disk_used_percent is not None and self.disk_used_percent < 100:
            free_percent = 100 - self.disk_used_percent
            if free_percent > 0:
                return round((self.available_capacity_gb * 100) / free_percent, 2)
        return round(self.available_capacity_gb, 2)

    @property
    def disk_used_gb(self):
        cap = float(self.disk_capacity_gb)
        used_pct = float(self.disk_used_percent)
        return round(cap * used_pct / 100.0, 2)

    @property
    def runway_info(self):
        growth = float(self.growth_gb or 0.0)
        free = float(self.available_capacity_gb or 0.0)
        if growth > 0.01 and free > 0:
            daily_burn = growth / 30.4
            days = int(free / daily_burn) if daily_burn > 0 else 9999
            if days <= 30:
                return {'days': days, 'text': f"{days} days", 'badge': 'critical'}
            elif days <= 90:
                return {'days': days, 'text': f"{days} days", 'badge': 'warning'}
            elif days <= 365:
                mos = round(days / 30.4, 1)
                return {'days': days, 'text': f"{mos} mos ({days}d)", 'badge': 'healthy'}
            else:
                return {'days': days, 'text': "> 1 Year", 'badge': 'stable'}
        return {'days': 9999, 'text': "Stable", 'badge': 'stable'}

    @property
    def runway_text(self):
        return self.runway_info['text']

    @property
    def runway_badge(self):
        return self.runway_info['badge']

    class Meta:
        db_table = 'capacity_planning'
        verbose_name = 'Capacity Planning'
        verbose_name_plural = 'Capacity Planning'
        default_permissions = ('add', 'view')
        permissions = [
            ('view_databasecapacity', 'Can view Database Capacity'),
            ('view_infrastructurecapacity', 'Can view Infrastructure Capacity'),
        ]

    def __str__(self):
        return f"{self.database} - {self.month}"

class SLAConfig(models.Model):
    month = models.DateField()
    database = models.CharField(max_length=50, choices=[
        ('MySQL', 'MySQL'),
        ('Oracle', 'Oracle'),
        ('MS SQL Server', 'MS SQL Server'),
    ])
    target_hours = models.DecimalField(max_digits=10, decimal_places=2)
    sla_percent = models.DecimalField(max_digits=5, decimal_places=2, default=99.95)
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'sla_config'
        verbose_name = 'SLA Configuration'
        verbose_name_plural = 'SLA Configurations'
        unique_together = ['month', 'database']

    def __str__(self):
        return f"{self.database} - {self.month} - {self.target_hours} hrs"

class UgandaHoliday(models.Model):
    holiday_date = models.DateField(primary_key=True)
    name = models.CharField(max_length=100)

    class Meta:
        db_table = 'uganda_holidays'
        verbose_name = 'Uganda Holiday'
        verbose_name_plural = 'Uganda Holidays'
        default_permissions = ('add', 'view')

    def __str__(self):
        return f"{self.name} - {self.holiday_date}"

    @property
    def is_fixed(self):
        return (self.holiday_date.month, self.holiday_date.day) in [
            (1, 1), (1, 26), (2, 16), (3, 8), (5, 1), (6, 3), (6, 9), (10, 9), (12, 25), (12, 26)
        ]

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import F

@receiver(post_save, sender=UgandaHoliday)
def update_target_hours_on_holiday_save(sender, instance, created, **kwargs):
    if created:
        first_day_of_month = instance.holiday_date.replace(day=1)
        is_weekend = instance.holiday_date.weekday() >= 5
        
        # Oracle: always subtract 18 hours for a holiday
        SLAConfig.objects.filter(month=first_day_of_month, database='Oracle').update(
            target_hours=F('target_hours') - 18
        )
        
        # Non-Oracle (MySQL, MSSQL, Postgres): only subtract 9 hours if it falls on a weekday
        if not is_weekend:
            SLAConfig.objects.filter(month=first_day_of_month).exclude(database='Oracle').update(
                target_hours=F('target_hours') - 9
            )

@receiver(post_delete, sender=UgandaHoliday)
def update_target_hours_on_holiday_delete(sender, instance, **kwargs):
    first_day_of_month = instance.holiday_date.replace(day=1)
    is_weekend = instance.holiday_date.weekday() >= 5
    
    # Oracle: always add 18 hours for a holiday
    SLAConfig.objects.filter(month=first_day_of_month, database='Oracle').update(
        target_hours=F('target_hours') + 18
    )
    
    # Non-Oracle: only add 9 hours if it fell on a weekday
    if not is_weekend:
        SLAConfig.objects.filter(month=first_day_of_month).exclude(database='Oracle').update(
            target_hours=F('target_hours') + 9
        )


class IncidentStaging(models.Model):
    category = models.CharField(max_length=20, choices=[
        ('Database', 'Database'),
        ('Infrastructure', 'Infrastructure'),
    ], default='Database')
    node_name = models.CharField(max_length=100, blank=True, default='')
    database = models.CharField(max_length=50, choices=[
        ('MySQL', 'MySQL'),
        ('Oracle', 'Oracle'),
        ('MS SQL Server', 'MS SQL Server'),
        ('Infrastructure', 'Infrastructure'),
    ], default='MySQL')
    system = models.CharField(max_length=100)
    start = models.DateTimeField()
    end = models.DateTimeField()
    duration_hrs = models.DecimalField(max_digits=10, decimal_places=2)
    root_cause = models.TextField(blank=True)
    status = models.CharField(max_length=50, choices=[
        ('Awaiting Review', 'Awaiting Review'),
        ('Promoted to Incident', 'Promoted to Incident'),
        ('Promoted to Maintenance', 'Promoted to Maintenance'),
    ], default='Awaiting Review')
    created_at = models.DateTimeField(auto_now_add=True)
    processed_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='processed_staging_incidents')
    processed_at = models.DateTimeField(null=True, blank=True)
    incident_record = models.ForeignKey('IncidentRegister', on_delete=models.SET_NULL, null=True, blank=True, related_name='staged_incidents')
    maintenance_record = models.ForeignKey('MaintenanceRegister', on_delete=models.SET_NULL, null=True, blank=True, related_name='staged_maintenances')

    class Meta:
        db_table = 'incident_staging'
        verbose_name = 'Incident Staging'
        verbose_name_plural = 'Incident Staging Records'
        default_permissions = ('add', 'view')

    def __str__(self):
        return f"Staged {self.database} ({self.start}) - {self.status}"

    @property
    def elapsed_duration_hrs(self):
        """Total wall-clock elapsed duration between start and end (24/7)."""
        if self.start and self.end and self.end > self.start:
            secs = (self.end - self.start).total_seconds()
            return round(secs / 3600.0, 2)
        return float(self.duration_hrs or 0.0)

    @property
    def elapsed_duration_mins(self):
        """Total wall-clock elapsed duration in minutes."""
        if self.start and self.end and self.end > self.start:
            secs = (self.end - self.start).total_seconds()
            return round(secs / 60.0, 1)
        return round(float(self.duration_hrs or 0.0) * 60.0, 1)

    @property
    def has_off_hours_difference(self):
        """Returns True if the elapsed wall-clock downtime differs noticeably from SLA business hours downtime."""
        try:
            return abs(float(self.elapsed_duration_hrs) - float(self.duration_hrs or 0.0)) >= 0.05
        except Exception:
            return False

    @property
    def off_hours_duration_hrs(self):
        """Duration of the outage that fell outside the SLA business operating window."""
        try:
            diff = float(self.elapsed_duration_hrs) - float(self.duration_hrs or 0.0)
            return round(max(0.0, diff), 2)
        except Exception:
            return 0.0

    @property
    def human_duration(self):
        """Clean human-readable SLA outage duration (e.g., '18 mins', '2h 18m', '< 1 min')."""
        return format_human_duration(self.duration_hrs)

    @property
    def human_elapsed_duration(self):
        """Clean human-readable total wall-clock elapsed duration (e.g., '5h 58m', '18 mins')."""
        return format_human_duration(self.elapsed_duration_hrs)

