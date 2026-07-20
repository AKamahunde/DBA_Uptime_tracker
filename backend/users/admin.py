from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User

class UserAdmin(BaseUserAdmin):
    list_display = ['username', 'email', 'is_admin', 'is_staff', 'is_active']
    list_filter = ['is_admin', 'is_staff', 'is_active']
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Custom Fields', {'fields': ('is_admin',)}),
    )

admin.site.register(User, UserAdmin)
