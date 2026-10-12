from django.conf import settings
from django.db import models


class NtfyConfig(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ntfy_config"
    )
    server_url = models.URLField(default="https://ntfy.sh")
    topic = models.SlugField(max_length=80)
    enabled = models.BooleanField(default=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)

    def __str__(self) -> str:
        return f"{self.user} -> {self.topic}"


class ReminderTime(models.Model):
    config = models.ForeignKey(NtfyConfig, on_delete=models.CASCADE, related_name="reminder_times")
    time = models.TimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["config", "time"], name="unique_reminder_time")
        ]
        ordering = ["time"]

    def __str__(self) -> str:
        return f"{self.config.user} @ {self.time}"


class NotificationLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_logs"
    )
    date = models.DateField()
    time = models.TimeField()
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "date", "time"], name="unique_notification_per_slot_per_day"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} - {self.date} {self.time}"
