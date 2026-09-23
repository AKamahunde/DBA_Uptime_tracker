from django import forms
from .models import (
    IncidentRegister, ReplicationMonitor, BackupRecovery, ServerHealth,
    MaintenanceRegister, CapacityPlanning, SLAConfig, UgandaHoliday
)

class IncidentRegisterForm(forms.ModelForm):
    change_comment = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Reason for change (Required if status or assignee changes)'}),
        required=False,
        label="Change Comment / Reason"
    )

    class Meta:
        model = IncidentRegister
        exclude = ['duration_hrs', 'created_by']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'start': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'end': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'root_cause': forms.Textarea(attrs={'rows': 3}),
            'business_impact': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        iso_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']
        if 'start' in self.fields:
            self.fields['start'].input_formats = iso_formats
        if 'end' in self.fields:
            self.fields['end'].input_formats = iso_formats

    def clean(self):
        cleaned_data = super().clean()
        
        # Only enforce on editing existing records
        if self.instance and self.instance.pk:
            old_status = self.instance.status
            old_assigned = self.instance.assigned_to
            
            new_status = cleaned_data.get('status')
            new_assigned = cleaned_data.get('assigned_to')
            comment = cleaned_data.get('change_comment', '').strip()
            
            status_changed = old_status != new_status
            assigned_changed = old_assigned != new_assigned
            
            if (status_changed or assigned_changed) and not comment:
                self.add_error('change_comment', forms.ValidationError(
                    "A change-reason comment is required whenever you change the status or assignee."
                ))
                
        return cleaned_data

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
            'date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'start': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'end': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'comments': forms.Textarea(attrs={'rows': 3}),
            'failure_reason': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Reason for backup failure (if any)'}),
            'location': forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['backup_type'].required = False
        self.fields['backup_used'].required = False
        self.fields['location'].required = False
        self.fields['comments'].required = False
        self.fields['failure_reason'].required = False
        self.fields['tested_by'].required = False
        iso_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']
        if 'start' in self.fields:
            self.fields['start'].input_formats = iso_formats
        if 'end' in self.fields:
            self.fields['end'].input_formats = iso_formats

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
    change_id = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Auto-generated (e.g., CHG2108)'}),
        label="Change ID"
    )

    class Meta:
        model = MaintenanceRegister
        exclude = ['created_by']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'start': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'end': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'activity': forms.Textarea(attrs={'rows': 3}),
            'rollback_plan': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'duration_hrs' in self.fields:
            self.fields['duration_hrs'].required = False
        if 'rollback_plan' in self.fields:
            self.fields['rollback_plan'].required = False
        iso_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']
        if 'start' in self.fields:
            self.fields['start'].input_formats = iso_formats
        if 'end' in self.fields:
            self.fields['end'].input_formats = iso_formats

class CapacityPlanningForm(forms.ModelForm):
    month = forms.CharField(widget=forms.TextInput(attrs={'type': 'month'}))

    class Meta:
        model = CapacityPlanning
        exclude = ['created_by']
        labels = {
            'database_size_gb': 'Database Size (GB)',
            'disk_used_percent': 'Disk Used %',
            'tablespace_used_percent': 'Tablespace Used %',
            'available_capacity_gb': 'Free Disk Space (GB)',
            'growth_gb': 'Growth (GB)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['database'].choices = [
            ('MySQL', 'MySQL'),
            ('Oracle', 'Oracle (nmsinfra)'),
            ('MS SQL Server', 'MS SQL Server'),
        ]
        if self.instance and self.instance.pk and self.instance.month:
            self.fields['month'].initial = self.instance.month.strftime('%Y-%m')

    def clean_month(self):
        m_str = self.cleaned_data.get('month')
        if m_str:
            from datetime import datetime
            try:
                # Appends -01 to save as a valid DateField (YYYY-MM-DD)
                return datetime.strptime(f"{m_str}-01", "%Y-%m-%d").date()
            except ValueError:
                raise forms.ValidationError("Enter a valid month (YYYY-MM).")
        return m_str

class SLAConfigForm(forms.ModelForm):
    month = forms.CharField(widget=forms.TextInput(attrs={'type': 'month'}))
    database = forms.ChoiceField(choices=[
        ('MySQL', 'MySQL'),
        ('Oracle', 'Oracle'),
        ('MS SQL Server', 'MS SQL Server'),
    ])
    target_hours = forms.DecimalField(
        required=False,
        max_digits=10,
        decimal_places=2,
        help_text="Leave blank to auto-calculate (Oracle: 18 hrs/day, 7 days/wk; Others: 9 hrs/day, 5 days/wk, excluding weekends and public holidays)."
    )

    class Meta:
        model = SLAConfig
        exclude = ['created_by']

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

    def clean(self):
        cleaned_data = super().clean()
        month_val = cleaned_data.get('month')
        target_hours = cleaned_data.get('target_hours')
        
        if month_val and target_hours is None:
            import calendar
            from datetime import date
            from .models import UgandaHoliday
            
            year = month_val.year
            month = month_val.month
            db = cleaned_data.get('database')
            
            # Fetch all holiday dates for this month as a set
            holidays = set(UgandaHoliday.objects.filter(
                holiday_date__year=year,
                holiday_date__month=month
            ).values_list('holiday_date', flat=True))
            
            _, num_days = calendar.monthrange(year, month)
            calculated_val = 0
            
            for day in range(1, num_days + 1):
                d = date(year, month, day)
                is_holiday = d in holidays
                is_weekend = d.weekday() >= 5  # 5 is Saturday, 6 is Sunday
                
                if not is_holiday:
                    if db == 'Oracle':
                        # Oracle: 18 hours/day, 7 days/week
                        calculated_val += 18
                    else:
                        # Non-Oracle: 9 hours/day, 5 days/week (Monday to Friday)
                        if not is_weekend:
                            calculated_val += 9
            
            cleaned_data['target_hours'] = calculated_val
            
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.database = self.cleaned_data.get('database')
        instance.target_hours = self.cleaned_data.get('target_hours')
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
