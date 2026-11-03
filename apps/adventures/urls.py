from django.urls import path

from . import views

app_name = "adventures"

urlpatterns = [
    path("adventures/", views.planner, name="planner"),
    path("adventures/weekend/<str:saturday>/", views.update_weekend, name="weekend"),
    path("adventures/goals/", views.goal_list, name="goals"),
    path("adventures/goals/new/", views.goal_add, name="goal_add"),
    path("adventures/goals/<int:pk>/edit/", views.goal_edit, name="goal_edit"),
    path("adventures/goals/<int:pk>/toggle/", views.goal_toggle, name="goal_toggle"),
    path("adventures/trips/", views.trip_list, name="trips"),
    path("adventures/trips/new/", views.trip_add, name="trip_add"),
    path("adventures/trips/<int:pk>/edit/", views.trip_edit, name="trip_edit"),
    path("adventures/areas/", views.area_list, name="areas"),
    path("adventures/areas/refresh/", views.refresh_route_counts, name="refresh_routes"),
    path("adventures/openbeta/search/", views.openbeta_search, name="openbeta_search"),
    path("adventures/ticks/", views.ticks, name="ticks"),
    path("adventures/areas/new/", views.area_add, name="area_add"),
    path("adventures/areas/<int:pk>/edit/", views.area_edit, name="area_edit"),
    path("adventures/<str:kind>/<int:pk>/delete/", views.delete, name="delete"),
]
