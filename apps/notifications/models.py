from datetime import time

from django.conf import settings
from django.db import models


class NtfyConfig(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ntfy_config"
    )
    server_url = models.URLField(default="https://ntfy.sh")
    topic = models.SlugField(max_length=80)
    reminder_time = models.TimeField(default=time(20, 0))
    enabled = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"{self.user} -> {self.topic}"


class NotificationLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_logs"
    )
    date = models.DateField()
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "date"], name="unique_notification_per_day")
        ]

    def __str__(self) -> str:
        return f"{self.user} - {self.date}"
