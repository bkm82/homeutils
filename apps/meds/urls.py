from django.urls import path

from . import views

app_name = "meds"

urlpatterns = [
    path("meds/", views.dashboard, name="dashboard"),
    path("meds/new/", views.add_medication, name="add"),
    path("meds/<int:pk>/toggle/", views.toggle_today, name="toggle"),
    path("meds/<int:pk>/archive/", views.archive_medication, name="archive"),
]
