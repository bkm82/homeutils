from django.contrib.auth.forms import AuthenticationForm
from django.forms import PasswordInput, TextInput


class StyledAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget = TextInput(
            attrs={"placeholder": "Username", "autofocus": True}
        )
        self.fields["password"].widget = PasswordInput(attrs={"placeholder": "Password"})
