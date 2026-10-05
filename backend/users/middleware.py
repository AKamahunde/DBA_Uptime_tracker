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

            # Exempt strictly the password change URL, logout URLs, and asset paths
            exempt_prefixes = [
                change_url,
                logout_url,
                '/admin/logout/',
            ]

            static_url = getattr(settings, 'STATIC_URL', '/static/')
            if static_url and static_url != '/':
                if not static_url.startswith('/'):
                    static_url = '/' + static_url
                exempt_prefixes.append(static_url)

            media_url = getattr(settings, 'MEDIA_URL', '')
            if media_url and media_url != '/':
                if not media_url.startswith('/'):
                    media_url = '/' + media_url
                exempt_prefixes.append(media_url)

            path = request.path
            is_exempt = any(path.startswith(prefix) for prefix in exempt_prefixes)

            if not is_exempt:
                return redirect('password_change_required')

        return self.get_response(request)
