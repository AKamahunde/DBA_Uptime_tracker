from django import forms
from .models import (
    IncidentRegister, ReplicationMonitor, BackupRecovery, ServerHealth,
    MaintenanceRegister, CapacityPlanning, TargetHours, UgandaHoliday
)

class IncidentRegisterForm(forms.ModelForm):
    change_comment = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional reason for change (e.g., Escalating to senior dev)'}),
        required=False,
        label="Change Comment / Reason"
    )

    class Meta:
        model = IncidentRegister
        exclude = ['planned', 'duration_hrs', 'created_by']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'start': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'end': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'root_cause': forms.Textarea(attrs={'rows': 3}),
            'business_impact': forms.Textarea(attrs={'rows': 3}),
        }

class ReplicationMonitorForm(forms.ModelForm):
    class Meta:
        model = ReplicationMonitor
        fields = [
            'date', 'master', 'replica',
            'io_thread', 'sql_thread', 'seconds_behind',
            'last_error', 'database', 'severity',
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'last_error': forms.Textarea(attrs={'rows': 3}),
        }

class BackupRecoveryForm(forms.ModelForm):
    class Meta:
        model = BackupRecovery
        exclude = ['created_by']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'start': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'end': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'comments': forms.Textarea(attrs={'rows': 3}),
            'location': forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['backup_type'].required = False
        self.fields['backup_used'].required = False
        self.fields['location'].required = False
        self.fields['comments'].required = False
        self.fields['tested_by'].required = False

class ServerHealthForm(forms.ModelForm):
    class Meta:
        model = ServerHealth
        fields = [
            'date', 'entered_by', 'server', 'cpu_percent', 'memory_percent', 
            'disk_percent', 'tablespace_percent', 'connections', 'slow_queries', 
            'threads', 'status', 'database'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
        }

class MaintenanceRegisterForm(forms.ModelForm):
    class Meta:
        model = MaintenanceRegister
        exclude = ['created_by']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'start': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'end': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'activity': forms.Textarea(attrs={'rows': 3}),
            'rollback_plan': forms.Textarea(attrs={'rows': 3}),
        }

class CapacityPlanningForm(forms.ModelForm):
    class Meta:
        model = CapacityPlanning
        exclude = ['created_by']
        widgets = {
            'month': forms.DateInput(attrs={'type': 'month'}),
        }

class TargetHoursForm(forms.ModelForm):
    month = forms.CharField(widget=forms.TextInput(attrs={'type': 'month'}))
    database = forms.ChoiceField(choices=[
        ('MySQL', 'MySQL'),
        ('Oracle', 'Oracle'),
        ('MS SQL Server', 'MS SQL Server'),
        ('PostgreSQL', 'PostgreSQL'),
    ])

    class Meta:
        model = TargetHours
        exclude = ['created_by', 'database_type']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.month:
            self.fields['month'].initial = self.instance.month.strftime('%Y-%m')

    def clean_month(self):
        m_str = self.cleaned_data.get('month')
        if m_str:
            from datetime import datetime
            try:
                return datetime.strptime(f"{m_str}-01", "%Y-%m-%d").date()
            except ValueError:
                raise forms.ValidationError("Enter a valid month (YYYY-MM).")
        return m_str

    def save(self, commit=True):
        instance = super().save(commit=False)
        db_map = {
            'MySQL': 'MYSQL',
            'Oracle': 'ORACLE',
            'MS SQL Server': 'MS SQL SERVER',
            'PostgreSQL': 'POSTGRESQL',
        }
        db_val = self.cleaned_data.get('database')
        instance.database_type = db_map.get(db_val, 'MYSQL')
        if commit:
            instance.save()
        return instance

class UgandaHolidayForm(forms.ModelForm):
    class Meta:
        model = UgandaHoliday
        fields = ['holiday_date', 'name']
        widgets = {
            'holiday_date': forms.DateInput(attrs={'type': 'date'}),
        }
