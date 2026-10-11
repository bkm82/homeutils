from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.urls import path

from .forms import StyledAuthenticationForm

app_name = "accounts"

urlpatterns = [
    path(
        "login/",
        login_not_required(
            auth_views.LoginView.as_view(
                template_name="accounts/login.html",
                authentication_form=StyledAuthenticationForm,
            )
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
