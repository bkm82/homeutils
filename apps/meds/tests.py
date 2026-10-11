from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import selectors
from .models import DoseLog, Medication

User = get_user_model()


def _log(medication, day, taken=True):
    DoseLog.objects.create(medication=medication, date=day, taken=taken)


class SelectorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="pw")
        self.today = date(2026, 1, 15)
        self.medication = Medication.objects.create(owner=self.user, name="Vitamin D")

    def test_streak_counts_consecutive_days_including_pending_today(self):
        for n in range(1, 4):
            _log(self.medication, self.today - timedelta(days=n))
        self.assertEqual(selectors.current_streak(self.medication, self.today), 3)

    def test_streak_includes_today_once_logged(self):
        for n in range(0, 4):
            _log(self.medication, self.today - timedelta(days=n))
        self.assertEqual(selectors.current_streak(self.medication, self.today), 4)

    def test_streak_breaks_on_gap(self):
        _log(self.medication, self.today - timedelta(days=1))
        _log(self.medication, self.today - timedelta(days=3))
        self.assertEqual(selectors.current_streak(self.medication, self.today), 1)

    def test_streak_zero_when_nothing_logged(self):
        self.assertEqual(selectors.current_streak(self.medication, self.today), 0)

    def test_streak_is_not_limited_by_when_the_medication_record_was_added(self):
        # The medication row itself was just created "today" in setUp, but a
        # user should still be able to backfill a streak that started well
        # before they got around to adding it to the app.
        for n in range(0, 10):
            _log(self.medication, self.today - timedelta(days=n))
        self.assertEqual(selectors.current_streak(self.medication, self.today), 10)

    def test_completion_rate_over_window(self):
        for n in range(0, 30, 2):
            _log(self.medication, self.today - timedelta(days=n))
        self.assertEqual(selectors.completion_rate(self.medication, 30, self.today), 50)

    def test_heatmap_reflects_backfilled_days_regardless_of_record_age(self):
        far_back_day = self.today - timedelta(days=60)
        _log(self.medication, far_back_day)
        cells = selectors.heatmap(self.medication, weeks=52, today=self.today)
        statuses = {cell.date: cell.status for cell in cells}
        self.assertEqual(statuses[far_back_day], "taken")
        self.assertEqual(statuses[self.today], "today-pending")


class ToggleDoseViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="pw")
        self.client.force_login(self.user)
        self.medication = Medication.objects.create(owner=self.user, name="Vitamin D")

    def test_toggle_without_date_logs_today(self):
        self.client.post(reverse("meds:toggle", args=[self.medication.pk]))
        log = DoseLog.objects.get(medication=self.medication)
        self.assertEqual(log.date, timezone.localdate())
        self.assertTrue(log.taken)

    def test_toggling_same_day_twice_flips_it_back_off(self):
        url = reverse("meds:toggle", args=[self.medication.pk])
        self.client.post(url)
        self.client.post(url)
        log = DoseLog.objects.get(medication=self.medication)
        self.assertFalse(log.taken)

    def test_can_backfill_a_past_date(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        self.client.post(
            reverse("meds:toggle", args=[self.medication.pk]),
            {"date": yesterday.isoformat()},
        )
        log = DoseLog.objects.get(medication=self.medication, date=yesterday)
        self.assertTrue(log.taken)

    def test_rejects_a_future_date(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        response = self.client.post(
            reverse("meds:toggle", args=[self.medication.pk]),
            {"date": tomorrow.isoformat()},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(DoseLog.objects.filter(medication=self.medication).exists())

    def test_rejects_a_malformed_date(self):
        response = self.client.post(
            reverse("meds:toggle", args=[self.medication.pk]),
            {"date": "not-a-date"},
        )
        self.assertEqual(response.status_code, 400)


class CrossUserIsolationTests(TestCase):
    """Users must never be able to view or mutate another user's medications."""

    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")
        self.alice_med = Medication.objects.create(owner=self.alice, name="Alice Pill")
        self.bob_med = Medication.objects.create(owner=self.bob, name="Bob Pill")

    def test_dashboard_only_shows_own_medications(self):
        self.client.force_login(self.bob)
        response = self.client.get(reverse("meds:dashboard"))
        self.assertNotContains(response, "Alice Pill")
        self.assertContains(response, "Bob Pill")

    def test_cannot_toggle_another_users_medication(self):
        self.client.force_login(self.bob)
        response = self.client.post(reverse("meds:toggle", args=[self.alice_med.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(DoseLog.objects.filter(medication=self.alice_med).exists())

    def test_cannot_archive_another_users_medication(self):
        self.client.force_login(self.bob)
        response = self.client.post(reverse("meds:archive", args=[self.alice_med.pk]))
        self.assertEqual(response.status_code, 404)
        self.alice_med.refresh_from_db()
        self.assertTrue(self.alice_med.is_active)

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("meds:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)
