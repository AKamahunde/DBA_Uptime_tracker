from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

class IncidentRegister(models.Model):
    ticket = models.CharField(max_length=50)
    date = models.DateField()
    start = models.DateTimeField()
    end = models.DateTimeField()
    duration_hrs = models.DecimalField(max_digits=10, decimal_places=2)
    planned = models.BooleanField(default=False)
    severity = models.CharField(max_length=20, choices=[
        ('Low', 'Low'),
        ('Medium', 'Medium'),
        ('High', 'High'),
        ('Critical', 'Critical')
    ])
    system = models.CharField(max_length=100)
    root_cause = models.TextField()
    business_impact = models.TextField()
    resolved_by = models.CharField(max_length=100)
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
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'incident_register'
        verbose_name = 'Incident Register'
        verbose_name_plural = 'Incident Registers'

    def __str__(self):
        return f"{self.ticket} - {self.date}"

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
    duration_hrs = models.DecimalField(max_digits=10, decimal_places=2)
    size_gb = models.DecimalField(max_digits=10, decimal_places=2)
    outcome = models.CharField(max_length=20, choices=[
        ('Success', 'Success'),
        ('Failed', 'Failed'),
        ('Partial', 'Partial')
    ])
    location = models.CharField(max_length=200)
    verified = models.BooleanField(default=False)
    restore_tested = models.BooleanField(default=False)
    compression = models.BooleanField(default=False)
    retention = models.CharField(max_length=50)
    tested_by = models.CharField(max_length=100, blank=True)
    comments = models.TextField(blank=True)
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'backup_recovery'
        verbose_name = 'Backup & Recovery'
        verbose_name_plural = 'Backup & Recovery'

    def __str__(self):
        return f"{self.record_type} - {self.database} - {self.date}"

class ServerHealth(models.Model):
    date = models.DateField()
    server = models.CharField(max_length=100)
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

    class Meta:
        db_table = 'maintenance_register'
        verbose_name = 'Maintenance Register'
        verbose_name_plural = 'Maintenance Registers'

    def __str__(self):
        return f"{self.change_id} - {self.date}"

class CapacityPlanning(models.Model):
    month = models.DateField()
    database_size_gb = models.DecimalField(max_digits=15, decimal_places=2)
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
    sla_percent = models.DecimalField(max_digits=5, decimal_places=2, default=99.9)
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

