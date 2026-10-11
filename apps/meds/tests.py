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
        self.medication = Medication.objects.create(
            owner=self.user, name="Vitamin D", start_date=self.today - timedelta(days=30)
        )

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

    def test_streak_stops_at_start_date(self):
        self.medication.start_date = self.today - timedelta(days=3)
        self.medication.save()
        for n in range(0, 10):
            _log(self.medication, self.today - timedelta(days=n))
        self.assertEqual(selectors.current_streak(self.medication, self.today), 4)

    def test_moving_start_date_earlier_extends_the_streak(self):
        # Editing start_date to reflect when you actually started taking the
        # medication (not just when the row was added) should immediately
        # extend the streak over already-backfilled days.
        for n in range(0, 10):
            _log(self.medication, self.today - timedelta(days=n))
        self.medication.start_date = self.today - timedelta(days=9)
        self.medication.save()
        self.assertEqual(selectors.current_streak(self.medication, self.today), 10)

    def test_completion_rate_over_window(self):
        for n in range(0, 30, 2):
            _log(self.medication, self.today - timedelta(days=n))
        self.assertEqual(selectors.completion_rate(self.medication, 30, self.today), 50)

    def test_completion_rate_clips_to_start_date(self):
        self.medication.start_date = self.today - timedelta(days=5)
        self.medication.save()
        for n in range(0, 6):
            _log(self.medication, self.today - timedelta(days=n))
        self.assertEqual(selectors.completion_rate(self.medication, 30, self.today), 100)

    def test_heatmap_marks_before_start_days(self):
        self.medication.start_date = self.today - timedelta(days=2)
        self.medication.save()
        cells = selectors.heatmap(self.medication, weeks=1, today=self.today)
        statuses = {cell.date: cell.status for cell in cells}
        self.assertEqual(statuses[self.today - timedelta(days=6)], "before-start")
        self.assertEqual(statuses[self.today], "today-pending")


class EditMedicationViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="pw")
        self.client.force_login(self.user)
        self.medication = Medication.objects.create(
            owner=self.user, name="Vitamin D", start_date=date(2026, 1, 1)
        )

    def test_get_prefills_existing_start_date(self):
        response = self.client.get(reverse("meds:edit", args=[self.medication.pk]))
        self.assertContains(response, "2026-01-01")

    def test_can_change_start_date(self):
        response = self.client.post(
            reverse("meds:edit", args=[self.medication.pk]),
            {"name": "Vitamin D", "note": "", "start_date": "2025-06-01"},
        )
        self.assertEqual(response.status_code, 302)
        self.medication.refresh_from_db()
        self.assertEqual(self.medication.start_date, date(2025, 6, 1))


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

    def test_cannot_edit_another_users_medication(self):
        self.client.force_login(self.bob)
        response = self.client.get(reverse("meds:edit", args=[self.alice_med.pk]))
        self.assertEqual(response.status_code, 404)

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
