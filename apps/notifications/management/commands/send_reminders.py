from urllib.error import URLError

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.meds.models import Medication
from apps.notifications.models import NotificationLog, NtfyConfig
from apps.notifications.ntfy import send_ntfy


class Command(BaseCommand):
    help = "Push an ntfy reminder to users with unlogged doses past their reminder time."

    def handle(self, *args, **options):
        now = timezone.localtime()
        today = now.date()

        for config in NtfyConfig.objects.filter(enabled=True, reminder_time__lte=now.time()):
            if not config.topic:
                continue
            if NotificationLog.objects.filter(user=config.user, date=today).exists():
                continue

            pending = Medication.objects.filter(owner=config.user, is_active=True).exclude(
                logs__date=today, logs__taken=True
            )
            if not pending.exists():
                continue

            try:
                send_ntfy(
                    config.server_url,
                    config.topic,
                    "Medication reminder",
                    ", ".join(pending.values_list("name", flat=True)),
                    priority="high",
                )
            except URLError as exc:
                self.stderr.write(f"Failed to notify {config.user}: {exc}")
                continue
            NotificationLog.objects.create(user=config.user, date=today)
