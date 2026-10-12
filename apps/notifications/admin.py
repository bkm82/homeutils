from django.contrib import admin

from .models import NotificationLog, NtfyConfig, ReminderTime


class ReminderTimeInline(admin.TabularInline):
    model = ReminderTime
    extra = 0


@admin.register(NtfyConfig)
class NtfyConfigAdmin(admin.ModelAdmin):
    list_display = ["user", "topic", "enabled", "last_checked_at", "last_error"]
    list_filter = ["enabled"]
    inlines = [ReminderTimeInline]


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ["user", "date", "time", "sent_at"]
