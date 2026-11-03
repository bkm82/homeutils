from django.contrib import admin

from .models import Climber, Crag, Credit, ForecastCache, Tick, ToDo, Trip, Weekend, Zone


@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display = ["name", "is_local", "latitude", "longitude"]


@admin.register(Crag)
class CragAdmin(admin.ModelAdmin):
    list_display = ["name", "zone", "sandstone"]
    list_filter = ["zone"]


@admin.register(Climber)
class ClimberAdmin(admin.ModelAdmin):
    list_display = ["name", "user", "is_active"]


@admin.register(ToDo)
class ToDoAdmin(admin.ModelAdmin):
    list_display = ["name", "climber", "crag", "grade", "rank", "is_project", "sent_on"]
    list_filter = ["climber", "is_project"]


@admin.register(Weekend)
class WeekendAdmin(admin.ModelAdmin):
    list_display = ["saturday", "outcome", "saturday_crag", "sunday_crag", "focus"]


@admin.register(Credit)
class CreditAdmin(admin.ModelAdmin):
    list_display = ["weekend", "climber", "value", "share"]


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ["title", "zone", "start_date", "end_date"]


@admin.register(Tick)
class TickAdmin(admin.ModelAdmin):
    list_display = ["route", "climber", "rating", "lead_style", "date"]


@admin.register(ForecastCache)
class ForecastCacheAdmin(admin.ModelAdmin):
    list_display = ["zone", "fetched_at"]
