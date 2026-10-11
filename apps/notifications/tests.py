from datetime import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.meds.models import DoseLog, Medication

from .models import NotificationLog, NtfyConfig

User = get_user_model()

SEND_NTFY_PATH = "apps.notifications.management.commands.send_reminders.send_ntfy"
TIMEZONE_PATH = "apps.notifications.management.commands.send_reminders.timezone"


def _fixed_now():
    return timezone.make_aware(datetime(2026, 1, 15, 21, 0))


class SendRemindersCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="pw")
        self.medication = Medication.objects.create(owner=self.user, name="Vitamin D")

    def _run(self):
        with patch(TIMEZONE_PATH) as mock_timezone, patch(SEND_NTFY_PATH) as mock_send:
            mock_timezone.localtime.return_value = _fixed_now()
            call_command("send_reminders")
        return mock_send

    def test_sends_when_dose_unlogged_past_reminder_time(self):
        NtfyConfig.objects.create(user=self.user, topic="t", reminder_time="20:00")
        mock_send = self._run()
        mock_send.assert_called_once()
        self.assertTrue(
            NotificationLog.objects.filter(user=self.user, date=_fixed_now().date()).exists()
        )

    def test_does_not_send_before_reminder_time(self):
        NtfyConfig.objects.create(user=self.user, topic="t", reminder_time="22:00")
        mock_send = self._run()
        mock_send.assert_not_called()

    def test_does_not_send_twice_same_day(self):
        NtfyConfig.objects.create(user=self.user, topic="t", reminder_time="20:00")
        self._run()
        mock_send = self._run()
        mock_send.assert_not_called()

    def test_does_not_send_when_everything_already_logged(self):
        NtfyConfig.objects.create(user=self.user, topic="t", reminder_time="20:00")
        DoseLog.objects.create(medication=self.medication, date=_fixed_now().date(), taken=True)
        mock_send = self._run()
        mock_send.assert_not_called()

    def test_does_not_send_when_disabled(self):
        NtfyConfig.objects.create(user=self.user, topic="t", reminder_time="20:00", enabled=False)
        mock_send = self._run()
        mock_send.assert_not_called()

    def test_message_only_includes_own_medications(self):
        bob = User.objects.create_user("bob", password="pw")
        Medication.objects.create(owner=bob, name="Bob Pill")
        NtfyConfig.objects.create(user=self.user, topic="t", reminder_time="20:00")
        mock_send = self._run()
        message = mock_send.call_args.args[3]
        self.assertIn("Vitamin D", message)
        self.assertNotIn("Bob Pill", message)


class NotificationSettingsViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="pw")
        self.client.force_login(self.user)

    def test_get_creates_config_on_first_visit(self):
        response = self.client.get(reverse("notifications:settings"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(NtfyConfig.objects.filter(user=self.user).exists())

    def test_post_updates_own_config(self):
        self.client.post(
            reverse("notifications:settings"),
            {"topic": "my-topic", "server_url": "https://ntfy.sh", "reminder_time": "19:30"},
        )
        config = NtfyConfig.objects.get(user=self.user)
        self.assertEqual(config.topic, "my-topic")

    def test_each_user_gets_their_own_config(self):
        bob = User.objects.create_user("bob", password="pw")
        self.client.post(
            reverse("notifications:settings"),
            {"topic": "alice-topic", "server_url": "https://ntfy.sh", "reminder_time": "19:30"},
        )
        self.client.force_login(bob)
        response = self.client.get(reverse("notifications:settings"))
        self.assertNotContains(response, "alice-topic")

    @patch("apps.notifications.views.send_ntfy")
    def test_send_test_uses_saved_config(self, mock_send):
        NtfyConfig.objects.create(user=self.user, topic="my-topic")
        self.client.post(reverse("notifications:test"))
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.args[1], "my-topic")

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("notifications:settings"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)
