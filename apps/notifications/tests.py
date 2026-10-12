from datetime import datetime
from unittest.mock import patch
from urllib.error import URLError

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.meds.models import DoseLog, Medication

from .models import NotificationLog, NtfyConfig, ReminderTime

User = get_user_model()

SEND_NTFY_PATH = "apps.notifications.management.commands.send_reminders.send_ntfy"
TIMEZONE_PATH = "apps.notifications.management.commands.send_reminders.timezone"


def _fixed_now():
    return timezone.make_aware(datetime(2026, 1, 15, 21, 0))


class SendRemindersCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="pw")
        self.medication = Medication.objects.create(owner=self.user, name="Vitamin D")

    def _config(self, **kwargs):
        kwargs.setdefault("topic", "t")
        return NtfyConfig.objects.create(user=self.user, **kwargs)

    def _run(self):
        with patch(TIMEZONE_PATH) as mock_timezone, patch(SEND_NTFY_PATH) as mock_send:
            mock_timezone.localtime.return_value = _fixed_now()
            call_command("send_reminders")
        return mock_send

    def test_sends_when_dose_unlogged_past_reminder_time(self):
        config = self._config()
        ReminderTime.objects.create(config=config, time="20:00")
        mock_send = self._run()
        mock_send.assert_called_once()
        self.assertTrue(
            NotificationLog.objects.filter(
                user=self.user, date=_fixed_now().date(), time="20:00"
            ).exists()
        )

    def test_does_not_send_before_reminder_time(self):
        config = self._config()
        ReminderTime.objects.create(config=config, time="22:00")
        mock_send = self._run()
        mock_send.assert_not_called()

    def test_does_not_send_twice_for_the_same_time_same_day(self):
        config = self._config()
        ReminderTime.objects.create(config=config, time="20:00")
        self._run()
        mock_send = self._run()
        mock_send.assert_not_called()

    def test_does_not_send_when_everything_already_logged(self):
        config = self._config()
        ReminderTime.objects.create(config=config, time="20:00")
        DoseLog.objects.create(medication=self.medication, date=_fixed_now().date(), taken=True)
        mock_send = self._run()
        mock_send.assert_not_called()

    def test_does_not_send_when_disabled(self):
        config = self._config(enabled=False)
        ReminderTime.objects.create(config=config, time="20:00")
        mock_send = self._run()
        mock_send.assert_not_called()

    def test_message_only_includes_own_medications(self):
        bob = User.objects.create_user("bob", password="pw")
        Medication.objects.create(owner=bob, name="Bob Pill")
        config = self._config()
        ReminderTime.objects.create(config=config, time="20:00")
        mock_send = self._run()
        message = mock_send.call_args.args[3]
        self.assertIn("Vitamin D", message)
        self.assertNotIn("Bob Pill", message)

    def test_skips_configs_with_no_topic_saved(self):
        config = NtfyConfig.objects.create(user=self.user, topic="")
        ReminderTime.objects.create(config=config, time="20:00")
        mock_send = self._run()
        mock_send.assert_not_called()

    def test_multiple_reminder_times_each_fire_independently(self):
        config = self._config()
        ReminderTime.objects.create(config=config, time="08:00")
        ReminderTime.objects.create(config=config, time="20:00")
        mock_send = self._run()
        self.assertEqual(mock_send.call_count, 2)
        self.assertTrue(
            NotificationLog.objects.filter(
                user=self.user, date=_fixed_now().date(), time="08:00"
            ).exists()
        )
        self.assertTrue(
            NotificationLog.objects.filter(
                user=self.user, date=_fixed_now().date(), time="20:00"
            ).exists()
        )

    def test_a_later_time_does_not_fire_if_already_taken_by_then(self):
        config = self._config()
        ReminderTime.objects.create(config=config, time="08:00")
        ReminderTime.objects.create(config=config, time="20:00")
        # Simulate: the 8am reminder already fired, then the dose got logged
        # before the 8pm check runs.
        NotificationLog.objects.create(user=self.user, date=_fixed_now().date(), time="08:00")
        DoseLog.objects.create(medication=self.medication, date=_fixed_now().date(), taken=True)
        mock_send = self._run()
        mock_send.assert_not_called()

    def test_editing_a_reminder_time_is_treated_as_unfired_today(self):
        config = self._config()
        reminder = ReminderTime.objects.create(config=config, time="08:00")
        self._run()
        # The earlier time already fired today - now the user edits it.
        reminder.time = "20:00"
        reminder.save()
        mock_send = self._run()
        mock_send.assert_called_once()

    def test_last_checked_at_updates_even_when_nothing_is_due(self):
        config = self._config()
        ReminderTime.objects.create(config=config, time="23:00")
        self.assertIsNone(config.last_checked_at)
        self._run()
        config.refresh_from_db()
        self.assertIsNotNone(config.last_checked_at)

    def test_last_error_set_on_failure_and_cleared_on_next_success(self):
        config = self._config()
        ReminderTime.objects.create(config=config, time="20:00")

        with patch(TIMEZONE_PATH) as mock_timezone, patch(SEND_NTFY_PATH) as mock_send:
            mock_timezone.localtime.return_value = _fixed_now()
            mock_send.side_effect = URLError("boom")
            call_command("send_reminders")
        config.refresh_from_db()
        self.assertIn("boom", config.last_error)
        self.assertFalse(NotificationLog.objects.filter(user=self.user).exists())

        # Same slot is still "unfired" since the send failed - next run
        # succeeds and should clear the error.
        self._run()
        config.refresh_from_db()
        self.assertEqual(config.last_error, "")
        self.assertTrue(NotificationLog.objects.filter(user=self.user, time="20:00").exists())

    def test_a_failed_send_does_not_block_other_users(self):
        bob = User.objects.create_user("bob", password="pw")
        Medication.objects.create(owner=bob, name="Bob Pill")
        alice_config = self._config(topic="t-alice")
        ReminderTime.objects.create(config=alice_config, time="20:00")
        bob_config = NtfyConfig.objects.create(user=bob, topic="t-bob")
        ReminderTime.objects.create(config=bob_config, time="20:00")

        def fail_for_alice(server_url, topic, *args, **kwargs):
            if topic == "t-alice":
                raise URLError("boom")

        with patch(TIMEZONE_PATH) as mock_timezone, patch(SEND_NTFY_PATH) as mock_send:
            mock_timezone.localtime.return_value = _fixed_now()
            mock_send.side_effect = fail_for_alice
            call_command("send_reminders")

        self.assertEqual(mock_send.call_count, 2)
        self.assertFalse(
            NotificationLog.objects.filter(user=self.user, date=_fixed_now().date()).exists()
        )
        self.assertTrue(
            NotificationLog.objects.filter(user=bob, date=_fixed_now().date()).exists()
        )
        alice_config.refresh_from_db()
        self.assertIn("boom", alice_config.last_error)


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
            {"topic": "my-topic", "server_url": "https://ntfy.sh"},
        )
        config = NtfyConfig.objects.get(user=self.user)
        self.assertEqual(config.topic, "my-topic")

    def test_each_user_gets_their_own_config(self):
        bob = User.objects.create_user("bob", password="pw")
        self.client.post(
            reverse("notifications:settings"),
            {"topic": "alice-topic", "server_url": "https://ntfy.sh"},
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

    @patch("apps.notifications.views.send_ntfy")
    def test_send_test_before_any_save_does_not_crash(self, mock_send):
        response = self.client.post(reverse("notifications:test"))
        self.assertEqual(response.status_code, 302)
        mock_send.assert_not_called()

    @patch("apps.notifications.views.send_ntfy")
    def test_send_test_network_failure_does_not_crash(self, mock_send):
        NtfyConfig.objects.create(user=self.user, topic="my-topic")
        mock_send.side_effect = URLError("boom")
        response = self.client.post(reverse("notifications:test"), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Could not reach")

    def test_save_shows_confirmation_message(self):
        response = self.client.post(
            reverse("notifications:settings"),
            {"topic": "my-topic", "server_url": "https://ntfy.sh"},
            follow=True,
        )
        self.assertContains(response, "Saved.")

    def test_saved_settings_appear_in_summary(self):
        self.client.post(
            reverse("notifications:settings"),
            {"topic": "my-topic", "server_url": "https://ntfy.sh"},
        )
        response = self.client.get(reverse("notifications:settings"))
        self.assertContains(response, "my-topic")

    def test_add_reminder_time(self):
        response = self.client.post(
            reverse("notifications:add_time"), {"time": "08:00"}, follow=True
        )
        self.assertContains(response, "08:00")
        self.assertTrue(ReminderTime.objects.filter(config__user=self.user, time="08:00").exists())

    def test_adding_duplicate_time_shows_friendly_message_not_crash(self):
        config = NtfyConfig.objects.create(user=self.user, topic="t")
        ReminderTime.objects.create(config=config, time="08:00")
        response = self.client.post(
            reverse("notifications:add_time"), {"time": "08:00"}, follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already in your list")

    def test_remove_reminder_time(self):
        config = NtfyConfig.objects.create(user=self.user, topic="t")
        reminder = ReminderTime.objects.create(config=config, time="08:00")
        self.client.post(reverse("notifications:remove_time", args=[reminder.pk]))
        self.assertFalse(ReminderTime.objects.filter(pk=reminder.pk).exists())

    def test_cannot_remove_another_users_reminder_time(self):
        bob = User.objects.create_user("bob", password="pw")
        bob_config = NtfyConfig.objects.create(user=bob, topic="t")
        reminder = ReminderTime.objects.create(config=bob_config, time="08:00")
        response = self.client.post(reverse("notifications:remove_time", args=[reminder.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(ReminderTime.objects.filter(pk=reminder.pk).exists())

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("notifications:settings"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)
