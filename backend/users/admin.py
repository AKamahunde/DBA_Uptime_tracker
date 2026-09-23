from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm, AdminUserCreationForm
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

    def validate_password_for_user(self, user, password_field_name="password2"):
        # Loosen validation for temporary admin resets; only require basic length of 4 characters
        if hasattr(self, "cleaned_data") and not self.cleaned_data.get("set_usable_password", True):
            return
        password = self.cleaned_data.get(password_field_name)
        if password and len(password) < 4:
            self.add_error(
                password_field_name,
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

    def validate_password_for_user(self, user, password_field_name="password2"):
        # Loosen validation for temporary user creation; only require basic length of 4 characters
        if hasattr(self, "cleaned_data") and not self.cleaned_data.get("set_usable_password", True):
            return
        password = self.cleaned_data.get(password_field_name)
        if password and len(password) < 4:
            self.add_error(
                password_field_name,
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
    list_filter = ['is_admin', 'must_change_password', 'is_staff', 'is_active']
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Custom Settings', {'fields': ('is_admin', 'must_change_password')}),
    )
    actions = ['require_password_change']

    def save_model(self, request, obj, form, change):
        if not change:
            # Default newly created users in admin to must_change_password=True
            obj.must_change_password = True
        super().save_model(request, obj, form, change)

    @admin.action(description="Require selected users to change password on next login")
    def require_password_change(self, request, queryset):
        updated = queryset.update(must_change_password=True)
        self.message_user(request, f"{updated} user(s) will be required to change their password on next login.")


admin.site.register(User, UserAdmin)
