from django.contrib import admin

from .models import Area, Climber, Credit, ForecastCache, Tick, ToDo, Trip, Weekend


@admin.register(Area)
class AreaAdmin(admin.ModelAdmin):
    list_display = ["name", "latitude", "longitude", "sandstone"]


@admin.register(Climber)
class ClimberAdmin(admin.ModelAdmin):
    list_display = ["name", "user", "is_active"]


@admin.register(ToDo)
class ToDoAdmin(admin.ModelAdmin):
    list_display = ["name", "climber", "area", "grade", "rank", "is_project", "sent_on"]
    list_filter = ["climber", "is_project"]


@admin.register(Weekend)
class WeekendAdmin(admin.ModelAdmin):
    list_display = ["saturday", "outcome", "area", "focus"]


@admin.register(Credit)
class CreditAdmin(admin.ModelAdmin):
    list_display = ["weekend", "climber", "value", "share"]


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ["title", "area", "start_date", "end_date"]


@admin.register(Tick)
class TickAdmin(admin.ModelAdmin):
    list_display = ["route", "climber", "rating", "lead_style", "date"]


@admin.register(ForecastCache)
class ForecastCacheAdmin(admin.ModelAdmin):
    list_display = ["area", "fetched_at"]
