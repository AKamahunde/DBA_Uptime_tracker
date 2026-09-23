from django.shortcuts import redirect
from django.urls import reverse
from django.conf import settings

class PasswordChangeRequiredMiddleware:
    """
    Middleware that enforces a password change if the authenticated user has must_change_password=True.
    Redirects all non-exempt requests to the password change page until the user updates their password.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and getattr(request.user, 'must_change_password', False):
            try:
                change_url = reverse('password_change_required')
            except Exception:
                change_url = '/password-change/'

            try:
                logout_url = reverse('logout')
            except Exception:
                logout_url = '/logout/'

            # Exempt paths that the user can access while in forced password change state
            exempt_prefixes = [
                change_url,
                logout_url,
                '/admin/logout/',
                settings.STATIC_URL,
            ]
            if hasattr(settings, 'MEDIA_URL') and settings.MEDIA_URL:
                exempt_prefixes.append(settings.MEDIA_URL)

            path = request.path
            is_exempt = any(path.startswith(prefix) for prefix in exempt_prefixes)

            if not is_exempt:
                return redirect('password_change_required')

        return self.get_response(request)
