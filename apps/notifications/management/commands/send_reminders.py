from urllib.error import URLError

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.meds.models import Medication
from apps.notifications.models import NotificationLog, NtfyConfig
from apps.notifications.ntfy import send_ntfy


class Command(BaseCommand):
    help = "Push an ntfy reminder for each unlogged reminder time that's come due."

    def handle(self, *args, **options):
        now = timezone.localtime()
        today = now.date()

        for config in NtfyConfig.objects.filter(enabled=True).prefetch_related("reminder_times"):
            config.last_checked_at = now

            if config.topic:
                due_times = config.reminder_times.filter(time__lte=now.time())
            else:
                due_times = config.reminder_times.none()

            for reminder in due_times:
                already_sent = NotificationLog.objects.filter(
                    user=config.user, date=today, time=reminder.time
                ).exists()
                if already_sent:
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
                    config.last_error = str(exc)
                    continue
                else:
                    config.last_error = ""
                NotificationLog.objects.create(user=config.user, date=today, time=reminder.time)

            config.save(update_fields=["last_checked_at", "last_error"])
