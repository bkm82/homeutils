from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("notifications/", views.settings_view, name="settings"),
    path("notifications/times/add/", views.add_reminder_time, name="add_time"),
    path("notifications/times/<int:pk>/remove/", views.remove_reminder_time, name="remove_time"),
    path("notifications/test/", views.send_test, name="test"),
]
