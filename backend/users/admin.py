from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm, AdminUserCreationForm
from django.contrib.admin.forms import AdminPasswordChangeForm as AdminSelfPasswordChangeForm
from django.core.exceptions import ValidationError
from .models import User


class CustomAdminPasswordChangeForm(AdminPasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "password1" in self.fields:
            self.fields["password1"].help_text = (
                "Temporary password (minimum 4 characters). "
                "The user will be required to set a permanent compliant password upon first login."
            )

    def validate_password_for_user(self, user, *args, **kwargs):
        # Loosen validation for temporary admin resets; only require basic length of 4 characters
        if hasattr(self, "cleaned_data") and not self.cleaned_data.get("set_usable_password", True):
            return
        password = self.cleaned_data.get("password1") or self.cleaned_data.get("password2")
        if password and len(password) < 4:
            self.add_error(
                "password1",
                ValidationError("Temporary password must be at least 4 characters long.")
            )

    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = True
        if commit:
            user.save()
        return user


class CustomAdminSelfPasswordChangeForm(AdminSelfPasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "new_password1" in self.fields:
            self.fields["new_password1"].help_text = (
                "Temporary or new password (minimum 4 characters). "
                "The user will be required to set a permanent compliant password upon first login."
            )

    def validate_password_for_user(self, user, *args, **kwargs):
        password = self.cleaned_data.get("new_password1") or self.cleaned_data.get("new_password2")
        if password and len(password) < 4:
            self.add_error(
                "new_password1",
                ValidationError("Temporary password must be at least 4 characters long.")
            )

    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = True
        if commit:
            user.save()
        return user


class CustomAdminUserCreationForm(AdminUserCreationForm):
    class Meta(AdminUserCreationForm.Meta):
        model = User
        fields = ("username",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "password1" in self.fields:
            self.fields["password1"].help_text = (
                "Temporary password (minimum 4 characters). "
                "The user will be required to set a permanent compliant password upon first login."
            )

    def validate_password_for_user(self, user, *args, **kwargs):
        # Loosen validation for temporary user creation; only require basic length of 4 characters
        if hasattr(self, "cleaned_data") and not self.cleaned_data.get("set_usable_password", True):
            return
        password = self.cleaned_data.get("password1") or self.cleaned_data.get("password2")
        if password and len(password) < 4:
            self.add_error(
                "password1",
                ValidationError("Temporary password must be at least 4 characters long.")
            )

    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = True
        if commit:
            user.save()
        return user


class UserAdmin(BaseUserAdmin):
    add_form = CustomAdminUserCreationForm
    change_password_form = CustomAdminPasswordChangeForm
    list_display = ['username', 'email', 'is_admin', 'must_change_password', 'is_staff', 'is_active']
    list_editable = ['must_change_password']
    list_filter = ['is_admin', 'must_change_password', 'is_staff', 'is_active']
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Custom Settings', {'fields': ('is_admin', 'must_change_password')}),
    )
    actions = ['require_password_change', 'reset_to_temporary_password']

    def save_model(self, request, obj, form, change):
        if not change:
            # Default newly created users in admin to must_change_password=True
            obj.must_change_password = True
        super().save_model(request, obj, form, change)

    @admin.action(description="Require selected users to change password on next login")
    def require_password_change(self, request, queryset):
        updated = queryset.update(must_change_password=True)
        if request:
            self.message_user(request, f"{updated} user(s) will be required to change their password on next login.")

    @admin.action(description="Reset password to 'temp1234' and require change on next login")
    def reset_to_temporary_password(self, request, queryset):
        for user in queryset:
            user.set_password('temp1234')
            user.must_change_password = True
            user.save()
        if request:
            self.message_user(
                request,
                f"Successfully reset password to 'temp1234' for {queryset.count()} user(s). They will be required to change password on next login."
            )


admin.site.register(User, UserAdmin)
admin.site.password_change_form = CustomAdminSelfPasswordChangeForm
