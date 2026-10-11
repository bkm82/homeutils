from django.conf import settings
from django.contrib.auth.middleware import RemoteUserMiddleware


class AutheliaRemoteUserMiddleware(RemoteUserMiddleware):
    """Authenticates requests from the identity header an Authelia
    forward-auth proxy sets, instead of Django's own login form.

    Only activated when AUTH_MODE=remote_user. Must never run on a
    deployment reachable except through that proxy.
    """

    header = settings.REMOTE_USER_HEADER
