from django.contrib.auth.backends import RemoteUserBackend


class AutheliaUserBackend(RemoteUserBackend):
    """RemoteUserBackend that also syncs profile fields Authelia forwards
    as separate headers (Remote-Email, Remote-Name) onto the local User.
    """

    def configure_user(self, request, user, created=True):
        email = request.META.get("HTTP_REMOTE_EMAIL", "")
        name = request.META.get("HTTP_REMOTE_NAME", "")
        if email:
            user.email = email
        if name:
            user.first_name = name
        user.save()
        return user
