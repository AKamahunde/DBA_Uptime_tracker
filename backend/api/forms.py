from django import forms
from .models import (
    IncidentRegister, ReplicationMonitor, BackupRecovery, ServerHealth,
    MaintenanceRegister, CapacityPlanning, SLAConfig, UgandaHoliday
)

class IncidentRegisterForm(forms.ModelForm):
    change_comment = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional reason for change (e.g., Escalating to senior dev)'}),
        required=False,
        label="Change Comment / Reason"
    )

    class Meta:
        model = IncidentRegister
        exclude = ['duration_hrs', 'created_by']
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
