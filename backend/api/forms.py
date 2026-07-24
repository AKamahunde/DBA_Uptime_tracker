from django import forms
from .models import (
    IncidentRegister, ReplicationMonitor, BackupRecovery, ServerHealth,
    MaintenanceRegister, CapacityPlanning
)

class IncidentRegisterForm(forms.ModelForm):
    change_comment = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional reason for change (e.g., Escalating to senior dev)'}),
        required=False,
        label="Change Comment / Reason"
    )

    class Meta:
        model = IncidentRegister
        exclude = ['planned', 'duration_hrs']
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
        fields = '__all__'
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
        self.fields['retention'].required = False
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
        fields = '__all__'
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
        fields = '__all__'
        widgets = {
            'month': forms.DateInput(attrs={'type': 'month'}),
        }
