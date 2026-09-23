from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm
from .models import User

class CustomAdminPasswordChangeForm(AdminPasswordChangeForm):
    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = True
        if commit:
            user.save()
        return user

class UserAdmin(BaseUserAdmin):
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
