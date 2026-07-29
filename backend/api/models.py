from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone

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
    database = models.CharField(max_length=50, choices=[
        ('MySQL', 'MySQL'),
        ('Oracle', 'Oracle'),
        ('MS SQL Server', 'MS SQL Server'),
        ('PostgreSQL', 'PostgreSQL'),
    ])
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

        if self.end and self.start:
            diff = self.end - self.start
            self.duration_hrs = round(diff.total_seconds() / 3600.0, 2)
        elif self.start and self.status not in ['Resolved', 'Closed']:
            diff = timezone.now() - self.start
            self.duration_hrs = round(diff.total_seconds() / 3600.0, 2)
        else:
            self.duration_hrs = 0
            
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.ticket} - {self.date}"

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
        ('PostgreSQL', 'PostgreSQL'),
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
        ('PostgreSQL', 'PostgreSQL'),
    ])
    date = models.DateField()
    database_instance = models.CharField(max_length=100)
    backup_type = models.CharField(max_length=20, choices=[
        ('Full', 'Full'),
        ('Incremental', 'Incremental'),
        ('Differential', 'Differential')
    ])
    backup_used = models.CharField(max_length=50)
    start = models.DateTimeField()
    end = models.DateTimeField()
    duration_mins = models.DecimalField(max_digits=10, decimal_places=2)
    outcome = models.CharField(max_length=20, choices=[
        ('Success', 'Success'),
        ('Failed', 'Failed'),
        ('Partial', 'Partial')
    ])
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
    server = models.CharField(max_length=100)
    entered_by = models.CharField(max_length=20, choices=[
        ('User', 'User'),
        ('Script', 'Script')
    ], default='User')
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
    disk_percent = models.DecimalField(
        max_digits=5, 
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    tablespace_percent = models.DecimalField(
        max_digits=5, 
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    connections = models.IntegerField()
    slow_queries = models.IntegerField()
    threads = models.IntegerField()
    status = models.CharField(max_length=20, choices=[
        ('Healthy', 'Healthy'),
        ('Warning', 'Warning'),
        ('Critical', 'Critical')
    ])
    database = models.CharField(max_length=50, choices=[
        ('MySQL', 'MySQL'),
        ('Oracle', 'Oracle'),
        ('MS SQL Server', 'MS SQL Server'),
        ('PostgreSQL', 'PostgreSQL'),
    ])
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'server_health'
        verbose_name = 'Server Health'
        verbose_name_plural = 'Server Health'
        default_permissions = ('add', 'view')

    def __str__(self):
        return f"{self.server} - {self.date}"

class MaintenanceRegister(models.Model):
    date = models.DateField()
    change_id = models.CharField(max_length=50)
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
    approved_by = models.CharField(max_length=100)
    success = models.BooleanField(default=False)
    database = models.CharField(max_length=50, choices=[
        ('MySQL', 'MySQL'),
        ('Oracle', 'Oracle'),
        ('MS SQL Server', 'MS SQL Server'),
        ('PostgreSQL', 'PostgreSQL'),
    ])
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self.start and self.end:
            delta = self.end - self.start
            # Convert timedelta to hours
            self.duration_hrs = round(delta.total_seconds() / 3600, 2)
        elif not self.duration_hrs:
            self.duration_hrs = 0
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
        ('PostgreSQL', 'PostgreSQL'),
    ])
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'capacity_planning'
        verbose_name = 'Capacity Planning'
        verbose_name_plural = 'Capacity Planning'
        default_permissions = ('add', 'view')

    def __str__(self):
        return f"{self.database} - {self.month}"

class TargetHours(models.Model):
    month = models.DateField()
    database = models.CharField(max_length=50)
    target_hours = models.DecimalField(max_digits=10, decimal_places=2)
    database_type = models.CharField(max_length=20, choices=[
        ('ORACLE', 'Oracle'),
        ('MYSQL', 'MySQL'),
        ('MS SQL SERVER', 'MS SQL Server'),
        ('POSTGRESQL', 'PostgreSQL'),
    ])
    sla_percent = models.DecimalField(max_digits=5, decimal_places=2, default=99.95)
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'target_hours'
        verbose_name = 'Target Hours'
        verbose_name_plural = 'Target Hours'
        unique_together = ['month', 'database', 'database_type']

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
        TargetHours.objects.filter(month=first_day_of_month).update(
            target_hours=F('target_hours') - 18
        )

@receiver(post_delete, sender=UgandaHoliday)
def update_target_hours_on_holiday_delete(sender, instance, **kwargs):
    first_day_of_month = instance.holiday_date.replace(day=1)
    TargetHours.objects.filter(month=first_day_of_month).update(
        target_hours=F('target_hours') + 18
    )

