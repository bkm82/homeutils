from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("notifications/", views.settings_view, name="settings"),
    path("notifications/test/", views.send_test, name="test"),
]
