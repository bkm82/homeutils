from django.contrib import admin

from .models import NotificationLog, NtfyConfig


@admin.register(NtfyConfig)
class NtfyConfigAdmin(admin.ModelAdmin):
    list_display = ["user", "topic", "reminder_time", "enabled"]
    list_filter = ["enabled"]


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ["user", "date", "sent_at"]
