from django.contrib import admin

from .models import Area, ForecastCache, Goal, Trip, Weekend


@admin.register(Area)
class AreaAdmin(admin.ModelAdmin):
    list_display = ["name", "latitude", "longitude", "sandstone"]


@admin.register(Goal)
class GoalAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "area", "activity", "grade", "is_done"]
    list_filter = ["is_done", "activity", "owner"]


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ["title", "area", "activity", "start_date", "end_date"]


@admin.register(Weekend)
class WeekendAdmin(admin.ModelAdmin):
    list_display = ["saturday", "priority"]


@admin.register(ForecastCache)
class ForecastCacheAdmin(admin.ModelAdmin):
    list_display = ["area", "fetched_at"]
