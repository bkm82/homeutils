from django.urls import path

from . import views

app_name = "adventures"

urlpatterns = [
    path("adventures/", views.planner, name="planner"),
    path("adventures/season/", views.season, name="season"),
    path("adventures/weekend/<str:saturday>/", views.update_weekend, name="weekend"),
    path("adventures/weekend/<str:saturday>/log/", views.log_weekend, name="log"),
    path("adventures/weekend/<str:saturday>/log/todos/", views.log_todos, name="log_todos"),
    path("adventures/weekend/<str:saturday>/undo/", views.undo_log, name="undo_log"),
    path("adventures/climbers/", views.climber_list, name="climbers"),
    path("adventures/climbers/new/", views.climber_add, name="climber_add"),
    path("adventures/climbers/<int:pk>/", views.climber_detail, name="climber"),
    path("adventures/climbers/<int:pk>/edit/", views.climber_edit, name="climber_edit"),
    path(
        "adventures/climbers/<int:pk>/use-suggested/",
        views.climber_use_suggested,
        name="climber_use_suggested",
    ),
    path("adventures/climbers/<int:climber_pk>/todos/new/", views.todo_add, name="todo_add"),
    path(
        "adventures/climbers/<int:climber_pk>/todos/search/",
        views.climb_search,
        name="climb_search",
    ),
    path(
        "adventures/climbers/<int:climber_pk>/todos/from-openbeta/",
        views.todo_from_openbeta,
        name="todo_from_openbeta",
    ),
    path("adventures/zones/from-openbeta/", views.zone_from_openbeta, name="zone_from_openbeta"),
    path("adventures/todos/<int:pk>/edit/", views.todo_edit, name="todo_edit"),
    path("adventures/todos/<int:pk>/send/", views.todo_send, name="todo_send"),
    path("adventures/todos/<int:pk>/move/<str:direction>/", views.todo_move, name="todo_move"),
    path("adventures/trips/", views.trip_list, name="trips"),
    path("adventures/trips/new/", views.trip_add, name="trip_add"),
    path("adventures/trips/<int:pk>/edit/", views.trip_edit, name="trip_edit"),
    path("adventures/zones/", views.zone_list, name="zones"),
    path("adventures/zones/new/", views.zone_add, name="zone_add"),
    path("adventures/zones/<int:pk>/edit/", views.zone_edit, name="zone_edit"),
    path("adventures/crags/new/", views.crag_add, name="crag_add"),
    path("adventures/crags/<int:pk>/edit/", views.crag_edit, name="crag_edit"),
    path("adventures/crags/refresh/", views.refresh_route_counts, name="refresh_routes"),
    path("adventures/openbeta/search/", views.openbeta_search, name="openbeta_search"),
    path("adventures/<str:kind>/<int:pk>/delete/", views.delete, name="delete"),
]
