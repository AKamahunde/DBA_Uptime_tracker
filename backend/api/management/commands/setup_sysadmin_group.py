from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.auth import get_user_model

User = get_user_model()

SYSADMIN_PERMISSIONS = [
    # Server Health (Host nodes and guest VMs)
    'view_serverhealth',
    'add_serverhealth',
    'change_serverhealth',
    # Infrastructure Incidents & Staging triage
    'view_infrastructureincident',
    'add_infrastructureincident',
    'change_incidentregister',
    'view_incidentstaging',
    'add_incidentstaging',
    'change_incidentstaging',
    # Infrastructure Maintenance windows
    'view_infrastructuremaintenance',
    'add_infrastructuremaintenance',
    'change_maintenanceregister',
    # Infrastructure Capacity records
    'view_infrastructurecapacity',
    'add_capacityplanning',
]

EXCLUDED_DASHBOARD_PERMISSIONS = [
    'view_databasedashboard',
    'view_infrastructuredashboard',
]


class Command(BaseCommand):
    help = (
        "Creates or configures the 'System Administration' group with full infrastructure responsibilities "
        "and zero dashboard permissions, and optionally assigns members."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--add-user',
            nargs='+',
            type=str,
            help="One or more usernames to add to the 'System Administration' group."
        )
        parser.add_argument(
            '--revoke-dashboard-perms',
            action='store_true',
            help="Also revoke direct user-level dashboard permissions from the specified users."
        )

    def handle(self, *args, **options):
        group_name = 'System Administration'
        group, created = Group.objects.get_or_create(name=group_name)
        action_verb = "Created" if created else "Found existing"
        self.stdout.write(self.style.SUCCESS(f"{action_verb} group '{group_name}'."))

        # Assign system administration responsibility permissions
        perms = Permission.objects.filter(codename__in=SYSADMIN_PERMISSIONS, content_type__app_label='api')
        group.permissions.set(perms)

        # Ensure NO dashboard permissions are assigned to this group
        excluded_perms = Permission.objects.filter(codename__in=EXCLUDED_DASHBOARD_PERMISSIONS, content_type__app_label='api')
        group.permissions.remove(*excluded_perms)

        self.stdout.write(self.style.SUCCESS(
            f"Assigned {perms.count()} infrastructure responsibility permissions to '{group_name}'.\n"
            f"Dashboard permissions explicitly excluded (0 dashboard access)."
        ))

        # Add users if specified
        usernames = options.get('add_user') or []
        revoke_direct = options.get('revoke_dashboard_perms', False)

        for uname in usernames:
            try:
                user = User.objects.get(username=uname)
                user.groups.add(group)
                self.stdout.write(self.style.SUCCESS(f"  + Added user '{uname}' to '{group_name}'."))

                if revoke_direct:
                    user.user_permissions.remove(*excluded_perms)
                    if getattr(user, 'is_admin', False):
                        user.is_admin = False
                        user.save(update_fields=['is_admin'])
                        self.stdout.write(self.style.WARNING(
                            f"  * Note: Disabled is_admin flag on '{uname}' so dashboard remains restricted."
                        ))
                    self.stdout.write(f"    Direct dashboard permissions revoked from '{uname}'.")
                elif getattr(user, 'is_admin', False):
                    self.stdout.write(self.style.WARNING(
                        f"  ! Warning: User '{uname}' has 'is_admin=True'. Admins inherently see all dashboards. "
                        f"Set is_admin=False on this user (or pass --revoke-dashboard-perms) to restrict dashboard access."
                    ))
            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"  - User '{uname}' does not exist."))
