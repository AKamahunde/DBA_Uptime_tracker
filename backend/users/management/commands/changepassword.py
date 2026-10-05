import getpass
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

UserModel = get_user_model()


class Command(BaseCommand):
    help = "Change a user's password to a temporary password and require password change on next login."
    requires_migrations_checks = True

    def _get_pass(self, prompt="Password: "):
        p = getpass.getpass(prompt=prompt)
        if not p:
            raise CommandError("aborted")
        return p

    def add_arguments(self, parser):
        parser.add_argument(
            "username",
            nargs="?",
            help="Username to change password for; by default, current username.",
        )
        parser.add_argument(
            "--database",
            default="default",
            help='Specifies the database to use. Default is "default".',
        )
        parser.add_argument(
            "--no-force-change",
            action="store_true",
            help="Do not require the user to change password on next login.",
        )

    def handle(self, *args, **options):
        if options["username"]:
            username = options["username"]
        else:
            username = getpass.getuser()

        try:
            u = UserModel._default_manager.using(options["database"]).get(
                **{UserModel.USERNAME_FIELD: username}
            )
        except UserModel.DoesNotExist:
            raise CommandError("user '%s' does not exist" % username)

        self.stdout.write("Changing password for user '%s'" % u)

        MAX_TRIES = 3
        count = 0
        p1, p2 = 1, 2
        password_validated = False
        while (p1 != p2 or not password_validated) and count < MAX_TRIES:
            p1 = self._get_pass()
            p2 = self._get_pass("Password (again): ")
            if p1 != p2:
                self.stdout.write("Passwords do not match. Please try again.")
                count += 1
                continue
            if len(p2) < 4:
                self.stderr.write("Temporary password must be at least 4 characters long.")
                count += 1
            else:
                password_validated = True

        if count == MAX_TRIES:
            raise CommandError(
                "Aborting password change for user '%s' after %s attempts" % (u, count)
            )

        u.set_password(p1)
        if not options.get("no_force_change", False):
            u.must_change_password = True
        else:
            u.must_change_password = False

        u.save()

        if u.must_change_password:
            return f"Password changed successfully for user '{u}'. User will be required to change password on next login."
        else:
            return f"Password changed successfully for user '{u}'."
