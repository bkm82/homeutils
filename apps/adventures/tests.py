from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from . import conditions, grades, ledger, openbeta, selectors, weather
from .grades import ClimbingLevel
from .models import Area, Climber, Credit, Focus, ForecastCache, Tick, ToDo, Trip, Weekend
from .weather import DayForecast, parse_daily

User = get_user_model()

FETCH_PATH = "apps.adventures.weather.fetch_daily"
OPENBETA_QUERY_PATH = "apps.adventures.openbeta._query"
SATURDAY = date(2026, 11, 7)


def _day(day, **kwargs):
    values = {
        "code": 1,
        "high": 65,
        "low": 40,
        "precip": 0,
        "precip_chance": 0,
        "snowfall": 0,
        "wind": 5,
    }
    values.update(kwargs)
    return DayForecast(date=day, **values)


def _daily(start, days):
    """An Open-Meteo-shaped `daily` block from a list of override dicts."""
    forecasts = [_day(start + timedelta(days=i), **d) for i, d in enumerate(days)]
    return {
        "time": [f.date.isoformat() for f in forecasts],
        "weather_code": [f.code for f in forecasts],
        "temperature_2m_max": [f.high for f in forecasts],
        "temperature_2m_min": [f.low for f in forecasts],
        "precipitation_sum": [f.precip for f in forecasts],
        "precipitation_probability_max": [f.precip_chance for f in forecasts],
        "snowfall_sum": [f.snowfall for f in forecasts],
        "wind_speed_10m_max": [f.wind for f in forecasts],
    }


TICK_CSV = "\n".join(
    [
        '\ufeffDate,Route,Rating,Notes,URL,Pitches,Location,"Avg Stars","Your Stars",'
        'Style,"Lead Style","Route Type","Your Rating",Length,"Rating Code"',
        "2026-10-20,Crack,5.10c,,https://www.mountainproject.com/route/1,1,"
        '"Utah > Indian Creek",3,3,Lead,Onsight,Trad,,100,0',
        '2026-10-19,Slab,5.11b/c,,,1,"Arizona > Sedona",3,-1,Lead,Redpoint,Sport,,80,0',
        '2026-10-18,Boulder,V5,,,1,"Arizona > Flagstaff",3,-1,Send,,Boulder,,,0',
        "not-a-date,Junk,5.9,,,,,,,,,,,,",
    ]
)


class GradeTests(TestCase):
    def test_parse_yds(self):
        cases = {
            "5.9": 9.0,
            "5.9+": 9.33,
            "5.10a": 10.0,
            "5.10c": 10.5,
            "5.10a/b": 10.125,
            "5.10-": 10.125,
            "5.10": 10.375,
            "5.11+": 11.625,
            "5.12d R": 12.75,
            "V5": None,
            "WI4": None,
        }
        for rating, expected in cases.items():
            with self.subTest(rating=rating):
                self.assertEqual(grades.parse_yds(rating), expected)

    def test_yds_label_round_trips_letter_grades(self):
        for label in ["5.8", "5.9+", "5.10a", "5.10d", "5.11a", "5.13c"]:
            with self.subTest(label=label):
                self.assertEqual(grades.yds_label(grades.parse_yds(label)), label)

    def test_climbing_level_averages_top_three_recent_clean_leads(self):
        today = date(2026, 11, 1)

        def tick(grade, lead_style, days_ago=1):
            return Tick(date=today - timedelta(days=days_ago), grade=grade, lead_style=lead_style)

        ticks = [
            tick(10.5, "Onsight"),
            tick(10.25, "Flash"),
            tick(10.0, "Onsight"),
            tick(9.0, "Onsight"),
            tick(12.0, "Redpoint"),
            tick(12.5, "Fell/Hung"),  # not clean
            tick(13.0, "Onsight", days_ago=400),  # too old
        ]
        level = grades.climbing_level(ticks, today)
        self.assertEqual(level.onsight_label, "5.10b")
        self.assertEqual(level.redpoint_label, "5.11a")  # avg of 12.0, 10.5, 10.25
        self.assertEqual(level.volume_band_label, "5.9+–5.10b")

    def test_count_in_band_mixes_coarse_and_letter_grades(self):
        counts = {"5.10": 4, "5.10a": 2, "5.10+": 9, "V3": 50, "5.9": 1}
        self.assertEqual(grades.count_in_band(counts, (10.0, 10.5)), 6)

    def test_parse_tick_export(self):
        rows = grades.parse_tick_export(TICK_CSV)
        self.assertEqual([r["route"] for r in rows], ["Crack", "Slab", "Boulder"])
        self.assertEqual(rows[0]["lead_style"], "Onsight")
        self.assertIsNone(rows[2]["grade"])


class OpenBetaTests(TestCase):
    def test_grade_counts_sum_across_areas_and_skip_bad_ids(self):
        data = {
            "a0": {"aggregate": {"byGrade": [{"label": "5.10", "count": 2}]}},
            "a1": {"aggregate": {"byGrade": [{"label": "5.10", "count": 3}]}},
        }
        good = ["f166e672-4a52-56d3-94f1-14c876feb670", "19dfb197-abbe-5b60-abc0-83f875a4e73b"]
        with patch(OPENBETA_QUERY_PATH, return_value=data) as query:
            counts = openbeta.fetch_grade_counts([*good, '") { evil }'])
        self.assertEqual(counts, {"5.10": 5})
        self.assertNotIn("evil", query.call_args.args[0])

    def test_no_ids_means_no_request(self):
        with patch(OPENBETA_QUERY_PATH) as query:
            self.assertEqual(openbeta.fetch_grade_counts([]), {})
        query.assert_not_called()


class ConditionsTests(TestCase):
    def test_dry_mild_day_is_go(self):
        self.assertEqual(conditions.rate(_day(SATURDAY), []).verdict, "go")

    def test_rain_is_no(self):
        wet = _day(SATURDAY, precip=0.4, precip_chance=80, code=63)
        self.assertEqual(conditions.rate(wet, []).verdict, "no")

    def test_recent_rain_rules_out_sandstone_only(self):
        prior = [_day(SATURDAY - timedelta(days=1), precip=0.3)]
        dry = _day(SATURDAY)
        self.assertEqual(conditions.rate(dry, prior, sandstone=True).verdict, "no")
        self.assertEqual(conditions.rate(dry, prior).verdict, "go")

    def test_cold_day_is_no(self):
        self.assertEqual(conditions.rate(_day(SATURDAY, high=33), []).verdict, "no")


class WeatherTests(TestCase):
    def test_parse_skips_null_padded_days(self):
        daily = _daily(SATURDAY, [{}, {}])
        daily["temperature_2m_max"][1] = None
        self.assertEqual([d.date for d in parse_daily(daily)], [SATURDAY])

    def test_get_forecasts_uses_cache_until_stale(self):
        area = Area.objects.create(name="Crag", latitude=1, longitude=2)
        with patch(FETCH_PATH, return_value=[_daily(SATURDAY, [{}])]) as fetch:
            weather.get_forecasts([area])
            weather.get_forecasts([area])
        self.assertEqual(fetch.call_count, 1)

        area.latitude = 3
        with patch(FETCH_PATH, return_value=[_daily(SATURDAY, [{}])]) as fetch:
            weather.get_forecasts([area])
        self.assertEqual(fetch.call_count, 1)

    def test_fetch_failure_falls_back_to_stale_cache(self):
        area = Area.objects.create(name="Crag", latitude=1, longitude=2)
        ForecastCache.objects.create(
            area=area,
            latitude=1,
            longitude=2,
            fetched_at=date(2020, 1, 1).isoformat() + "T00:00Z",
            daily=_daily(SATURDAY, [{}]),
        )
        with patch(FETCH_PATH, side_effect=OSError("offline")):
            forecasts = weather.get_forecasts([area])
        self.assertEqual(len(forecasts[area.id]), 1)


def _todo(climber, area, rank, **kwargs):
    return ToDo.objects.create(
        climber=climber, area=area, rank=rank, name=f"{climber}-{area}-{rank}", **kwargs
    )


class LedgerTests(TestCase):
    def setUp(self):
        self.a = Area.objects.create(name="A", latitude=0, longitude=0)
        self.b = Area.objects.create(name="B", latitude=0, longitude=0)
        self.me = Climber.objects.create(name="Me")
        self.you = Climber.objects.create(name="You")

    def test_weights_follow_list_position_and_skip_sends(self):
        first = _todo(self.me, self.a, 1)
        sent = _todo(self.me, self.a, 2, sent_on=SATURDAY)
        third = _todo(self.me, self.b, 3)
        weights = ledger.todo_weights([first, sent, third])
        self.assertEqual(weights, {first.pk: 1.0, third.pk: 0.5})

    def test_crag_values_sum_weights_and_can_count_projects_only(self):
        todos = [
            _todo(self.me, self.a, 1, is_project=True),
            _todo(self.me, self.a, 2),
            _todo(self.me, self.b, 3),
        ]
        self.assertEqual(
            ledger.crag_values(todos),
            {(self.me.pk, self.a.pk): 1.5, (self.me.pk, self.b.pk): 1 / 3},
        )
        self.assertEqual(
            ledger.crag_values(todos, projects_only=True), {(self.me.pk, self.a.pk): 1.0}
        )

    def test_split_credit_gives_everyone_an_equal_share(self):
        values = {(self.me.pk, self.a.pk): 1.0}
        credits = ledger.split_credit([self.me.pk, self.you.pk], self.a.pk, values)
        self.assertEqual(credits, [(self.me.pk, 1.0, 0.5), (self.you.pk, 0.0, 0.5)])

    def test_balances_and_need(self):
        weekend = Weekend.objects.create(saturday=SATURDAY)
        Credit.objects.create(weekend=weekend, climber=self.me, value=1.0, share=0.5)
        Credit.objects.create(weekend=weekend, climber=self.you, value=0.0, share=0.5)
        balances = ledger.balances(Credit.objects.all())
        self.assertEqual(balances, {self.me.pk: -0.5, self.you.pk: 0.5})
        self.assertEqual(ledger.need_weight(0.5), 1.5)
        self.assertEqual(ledger.need_weight(-5), ledger.MIN_NEED_WEIGHT)


class SelectorTests(TestCase):
    def setUp(self):
        Area.objects.all().delete()  # drop the seeded starter areas
        self.peaks = Area.objects.create(name="Peaks", latitude=1, longitude=1)
        self.sedona = Area.objects.create(name="Sedona", latitude=2, longitude=2, sandstone=True)
        self.areas = [self.peaks, self.sedona]
        self.me = Climber.objects.create(name="Me")
        self.you = Climber.objects.create(name="You")
        self.climbers = [self.me, self.you]
        start = SATURDAY - timedelta(days=3)
        dry = parse_daily(_daily(start, [{}] * 5))
        self.forecasts = {self.peaks.id: dry, self.sedona.id: dry}

    def _weekend(self, attendees=None, balances=None, focus=Focus.NONE, levels=None, trips=()):
        weekend = Weekend(saturday=SATURDAY, focus=focus)
        return selectors.build_weekend(
            SATURDAY,
            weekend,
            self.climbers,
            self.climbers if attendees is None else attendees,
            self.areas,
            self.forecasts,
            list(ToDo.objects.filter(sent_on__isnull=True)),
            balances or {},
            levels or {},
            list(trips),
        )

    def test_weekend_saturdays(self):
        self.assertEqual(selectors.weekend_saturdays(date(2026, 11, 2))[0], SATURDAY)  # Mon
        self.assertEqual(selectors.weekend_saturdays(SATURDAY + timedelta(days=1))[0], SATURDAY)
        self.assertEqual(len(selectors.weekend_saturdays(date(2026, 11, 2))), 2)

    def test_crag_with_biggest_todos_wins(self):
        _todo(self.me, self.peaks, 1, is_project=True)
        _todo(self.you, self.sedona, 3)
        _todo(self.you, self.peaks, 1)
        top = self._weekend().options[0]
        self.assertEqual(top.area, self.peaks)
        self.assertEqual({s.climber for s in top.served}, {self.me, self.you})

    def test_whoever_is_owed_pulls_the_pick(self):
        _todo(self.me, self.peaks, 1)
        _todo(self.you, self.sedona, 1)
        top = self._weekend(balances={self.you.pk: 0.5, self.me.pk: -0.5}).options[0]
        self.assertEqual(top.area, self.sedona)

    def test_only_attendees_count(self):
        _todo(self.me, self.peaks, 1)
        _todo(self.you, self.sedona, 1)
        _todo(self.you, self.sedona, 2)
        top = self._weekend(attendees=[self.me]).options[0]
        self.assertEqual(top.area, self.peaks)

    def test_weather_outranks_fairness(self):
        _todo(self.me, self.sedona, 1)
        rain = {"precip": 0.5, "precip_chance": 90, "code": 63}
        start = SATURDAY - timedelta(days=3)
        self.forecasts[self.sedona.id] = parse_daily(_daily(start, [{}, {}, {}, rain, rain]))
        self.assertEqual(self._weekend().options[0].area, self.peaks)

    def test_try_hard_counts_only_projects(self):
        _todo(self.me, self.peaks, 1)
        _todo(self.me, self.peaks, 2)
        _todo(self.you, self.sedona, 2, is_project=True)
        self.assertEqual(self._weekend().options[0].area, self.peaks)
        self.assertEqual(self._weekend(focus=Focus.TRY_HARD).options[0].area, self.sedona)

    def test_volume_focus_ranks_by_routes_in_attendees_bands(self):
        _todo(self.me, self.peaks, 1)
        self.sedona.grade_counts = {"5.10": 40, "5.12a": 5}
        self.peaks.grade_counts = {"5.10a": 3}
        levels = {self.me.pk: ClimbingLevel(onsight=10.5, redpoint=11.5)}
        top = self._weekend(focus=Focus.VOLUME, levels=levels).options[0]
        self.assertEqual((top.area, top.volume_count), (self.sedona, 40))

    def test_rained_out_trip_suggests_pivots(self):
        rain = {"precip": 0.5, "precip_chance": 90, "code": 63}
        start = SATURDAY - timedelta(days=3)
        self.forecasts[self.sedona.id] = parse_daily(_daily(start, [{}, {}, {}, rain, rain]))
        trip = Trip.objects.create(
            title="Crew trip", area=self.sedona, start_date=SATURDAY, end_date=SATURDAY
        )
        [outlook] = self._weekend(trips=[trip]).trips
        self.assertEqual(outlook.option.verdict, "no")
        self.assertEqual([o.area for o in outlook.alternatives], [self.peaks])


class ViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("me", password="pw")
        self.client.force_login(self.user)
        self.peaks = Area.objects.get(name="Flagstaff")
        self.sedona = Area.objects.get(name="Sedona")
        self.me = Climber.objects.create(name="Me", user=self.user)
        self.you = Climber.objects.create(name="You")
        # Only weekends that have started can be logged.
        self.past = selectors.weekend_saturdays(timezone.localdate())[0] - timedelta(weeks=1)

    def _post_log(self, **data):
        data.setdefault("outcome", "climbed")
        data.setdefault("attendees", [self.me.pk, self.you.pk])
        return self.client.post(reverse("adventures:log", args=[self.past.isoformat()]), data)

    def test_planner_renders_with_api_down(self):
        with patch(FETCH_PATH, side_effect=OSError("offline")):
            response = self.client.get(reverse("adventures:planner"))
        self.assertContains(response, "No forecast available")

    def test_planner_shows_who_a_crag_serves(self):
        _todo(self.me, self.sedona, 1, is_project=True)

        def fake_fetch(points):
            return [_daily(timezone.localdate() - timedelta(days=3), [{}] * 19) for _ in points]

        with patch(FETCH_PATH, side_effect=fake_fetch):
            response = self.client.get(reverse("adventures:planner"))
        self.assertContains(response, "(project)")

    def test_toggle_attendee_starts_from_everyone(self):
        url = reverse("adventures:weekend", args=[SATURDAY.isoformat()])
        self.client.post(url, {"climber": self.you.pk})
        self.assertEqual(list(Weekend.objects.get().attendees.all()), [self.me])
        self.client.post(url, {"climber": self.you.pk})
        self.assertEqual(Weekend.objects.get().attendees.count(), 2)

    def test_set_focus(self):
        url = reverse("adventures:weekend", args=[SATURDAY.isoformat()])
        self.client.post(url, {"focus": "try_hard"})
        self.assertEqual(Weekend.objects.get().focus, "try_hard")
        self.assertEqual(self.client.post(url, {"focus": "rest"}).status_code, 400)

    def test_non_saturday_is_404(self):
        url = reverse("adventures:weekend", args=[(SATURDAY + timedelta(days=1)).isoformat()])
        self.assertEqual(self.client.post(url, {"focus": ""}).status_code, 404)

    def test_log_climbed_weekend_credits_and_records_send(self):
        project = _todo(self.me, self.peaks, 1, is_project=True)
        _todo(self.me, self.peaks, 2)
        _todo(self.you, self.sedona, 1)
        self._post_log(area=self.peaks.pk, worked=[project.pk], sent=[project.pk])

        credits = {c.climber: (c.value, c.share) for c in Credit.objects.all()}
        self.assertEqual(credits, {self.me: (1.5, 0.75), self.you: (0.0, 0.75)})
        project.refresh_from_db()
        self.assertEqual((project.sessions, project.sent_on), (1, self.past + timedelta(days=1)))
        # The send moved the remaining to-do up to #1.
        self.assertEqual(
            list(self.me.todos.filter(sent_on__isnull=True).values_list("rank", flat=True)), [1]
        )

        balances = selectors.season_balances()
        self.assertEqual(balances, {self.me.pk: -0.75, self.you.pk: 0.75})

    def test_log_ignores_todos_from_other_crags(self):
        elsewhere = _todo(self.me, self.sedona, 1)
        self._post_log(area=self.peaks.pk, sent=[elsewhere.pk])
        elsewhere.refresh_from_db()
        self.assertIsNone(elsewhere.sent_on)

    def test_rained_out_weekend_credits_nobody(self):
        _todo(self.me, self.peaks, 1)
        self._post_log(outcome="rained_out")
        self.assertEqual(Weekend.objects.get().outcome, "rained_out")
        self.assertFalse(Credit.objects.exists())

    def test_cannot_log_twice_without_undo(self):
        _todo(self.me, self.peaks, 1)
        self._post_log(area=self.peaks.pk)
        self.assertEqual(self._post_log(area=self.peaks.pk).status_code, 400)
        self.client.post(reverse("adventures:undo_log", args=[self.past.isoformat()]))
        self.assertFalse(Credit.objects.exists())
        self._post_log(outcome="rained_out")
        self.assertEqual(Weekend.objects.get().outcome, "rained_out")

    def test_log_requires_area_when_climbed(self):
        self._post_log()
        self.assertFalse(Weekend.objects.filter(outcome="climbed").exists())

    def test_cannot_log_a_future_weekend(self):
        future = selectors.weekend_saturdays(timezone.localdate())[-1] + timedelta(weeks=1)
        response = self.client.get(reverse("adventures:log", args=[future.isoformat()]))
        self.assertEqual(response.status_code, 400)

    def test_log_todos_partial(self):
        _todo(self.me, self.peaks, 1)
        url = reverse("adventures:log_todos", args=[SATURDAY.isoformat()])
        response = self.client.get(url, {"area": self.peaks.pk, "attendees": [self.me.pk]})
        self.assertContains(response, "Worked")

    def test_season_page(self):
        _todo(self.me, self.peaks, 1)
        self._post_log(area=self.peaks.pk)
        response = self.client.get(reverse("adventures:season"))
        self.assertContains(response, "owed 0.5")
        self.assertContains(response, "ahead 0.5")

    def test_add_todo_appends_and_move_reorders(self):
        first = _todo(self.me, self.peaks, 1)
        self.client.post(
            reverse("adventures:todo_add", args=[self.me.pk]),
            {"name": "New", "area": self.sedona.pk},
        )
        new = ToDo.objects.get(name="New")
        self.assertEqual(new.rank, 2)
        self.client.post(reverse("adventures:todo_move", args=[new.pk, "up"]))
        new.refresh_from_db()
        first.refresh_from_db()
        self.assertEqual((new.rank, first.rank), (1, 2))

    def test_send_toggle_renumbers(self):
        first = _todo(self.me, self.peaks, 1)
        second = _todo(self.me, self.peaks, 2)
        self.client.post(reverse("adventures:todo_send", args=[first.pk]))
        second.refresh_from_db()
        self.assertEqual(second.rank, 1)
        self.client.post(reverse("adventures:todo_send", args=[first.pk]))
        first.refresh_from_db()
        self.assertEqual((first.sent_on, first.rank), (None, 2))

    def test_tick_import_replaces_previous_and_shows_level(self):
        Tick.objects.create(climber=self.you, date=SATURDAY, route="Old", rating="5.8")
        upload = SimpleUploadedFile("ticks.csv", TICK_CSV.encode(), content_type="text/csv")
        self.client.post(reverse("adventures:climber", args=[self.you.pk]), {"file": upload})
        self.assertEqual(
            sorted(self.you.ticks.values_list("route", flat=True)), ["Boulder", "Crack", "Slab"]
        )

    def test_tick_import_suggests_but_does_not_set_grades(self):
        self.you.onsight_grade = "5.9"
        self.you.save()
        upload = SimpleUploadedFile("ticks.csv", TICK_CSV.encode(), content_type="text/csv")
        self.client.post(reverse("adventures:climber", args=[self.you.pk]), {"file": upload})
        self.you.refresh_from_db()
        self.assertEqual(self.you.onsight_grade, "5.9")
        # TICK_CSV's dates are fixed, so pin "today" inside the level window.
        with patch("apps.adventures.views.timezone.localdate", return_value=date(2026, 11, 1)):
            response = self.client.get(reverse("adventures:climber", args=[self.you.pk]))
            self.assertContains(response, "From their ticks: 5.10c onsight")
            self.client.post(reverse("adventures:climber_use_suggested", args=[self.you.pk]))
        self.you.refresh_from_db()
        # Redpoint suggestion averages the two clean sends: 5.11b/c and 5.10c.
        self.assertEqual((self.you.onsight_grade, self.you.redpoint_grade), ("5.10c", "5.11a"))

    def test_climber_grades_are_validated(self):
        response = self.client.post(
            reverse("adventures:climber_edit", args=[self.you.pk]),
            {"name": "You", "onsight_grade": "V5", "is_active": "on"},
        )
        self.assertContains(response, "Use a YDS grade")
        self.client.post(
            reverse("adventures:climber_edit", args=[self.you.pk]),
            {"name": "You", "onsight_grade": "5.10+", "redpoint_grade": "", "is_active": "on"},
        )
        self.you.refresh_from_db()
        self.assertEqual(self.you.onsight_grade, "5.10+")

    def test_planner_levels_come_from_entered_grades(self):
        self.me.onsight_grade = "5.10c"
        self.me.save()
        Tick.objects.create(
            climber=self.you,
            date=timezone.localdate(),
            route="R",
            grade=12.0,
            lead_style="Onsight",
        )
        levels = selectors.climber_levels([self.me, self.you])
        self.assertEqual(levels[self.me.pk].onsight_label, "5.10c")
        self.assertIsNone(levels[self.you.pk].onsight)

    def test_tick_import_rejects_non_export(self):
        upload = SimpleUploadedFile("x.csv", b"foo,bar\n1,2\n", content_type="text/csv")
        response = self.client.post(
            reverse("adventures:climber", args=[self.me.pk]), {"file": upload}
        )
        self.assertContains(response, "Expected a Mountain Project tick export")

    def test_climber_pages_render(self):
        _todo(self.me, self.peaks, 1)
        self.assertContains(self.client.get(reverse("adventures:climbers")), "Me")
        self.assertContains(
            self.client.get(reverse("adventures:climber", args=[self.me.pk])), "#1"
        )

    def test_add_climber_without_login(self):
        self.client.post(reverse("adventures:climber_add"), {"name": "Friend", "is_active": "on"})
        self.assertIsNone(Climber.objects.get(name="Friend").user)

    def test_refresh_route_counts(self):
        data = {"a0": {"aggregate": {"byGrade": [{"label": "5.10", "count": 7}]}}}
        with patch(OPENBETA_QUERY_PATH, return_value=data):
            self.client.post(reverse("adventures:refresh_routes"))
        self.assertEqual(Area.objects.get(name="Sedona").grade_counts, {"5.10": 7})

    def test_refresh_route_counts_survives_openbeta_outage(self):
        with patch(OPENBETA_QUERY_PATH, side_effect=OSError("down")):
            response = self.client.post(reverse("adventures:refresh_routes"), follow=True)
        self.assertContains(response, "Couldn&#x27;t reach OpenBeta")

    def test_area_form_rejects_bad_openbeta_id(self):
        response = self.client.post(
            reverse("adventures:area_add"),
            {"name": "X", "latitude": "1", "longitude": "2", "openbeta_uuids": "not-a-uuid"},
        )
        self.assertContains(response, "Not an OpenBeta area id")

    def test_delete_todo_renumbers(self):
        first = _todo(self.me, self.peaks, 1)
        second = _todo(self.me, self.peaks, 2)
        self.client.post(reverse("adventures:delete", args=["todo", first.pk]))
        second.refresh_from_db()
        self.assertEqual(second.rank, 1)


class GroupMigrationTests(TransactionTestCase):
    """0003/0004 carry v0.7.0's per-user goals and ticks over to climber profiles."""

    before = [("adventures", "0002_seed_areas")]
    after = [("adventures", "0004_drop_non_climbing")]

    def test_goals_and_ticks_become_climber_data(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        old = executor.loader.project_state(self.before).apps
        user = old.get_model("auth", "User").objects.create(username="pat", first_name="Pat")
        # Rolling back recreates `activities` empty; restore what 0002 seeded.
        OldArea = old.get_model("adventures", "Area")
        OldArea.objects.update(activities=["climbing"])
        OldArea.objects.filter(name="Arizona Snowbowl").update(activities=["skiing"])
        area = OldArea.objects.get(name="Sedona")
        Goal = old.get_model("adventures", "Goal")
        Goal.objects.create(owner_id=user.pk, name="Done one", area=area, is_done=True)
        Goal.objects.create(owner_id=user.pk, name="Open one", area=area)
        Goal.objects.create(owner_id=user.pk, name="Ski", area=area, activity="skiing")
        old.get_model("adventures", "Tick").objects.create(
            owner_id=user.pk, date=SATURDAY, route="R"
        )
        old.get_model("adventures", "Weekend").objects.create(saturday=SATURDAY, focus="rest")

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.after)
        new = executor.loader.project_state(self.after).apps
        climber = new.get_model("adventures", "Climber").objects.get()
        self.assertEqual((climber.name, climber.user_id), ("Pat", user.pk))
        todos = new.get_model("adventures", "ToDo").objects.order_by("rank")
        self.assertEqual(
            [(t.name, t.rank, t.sent_on is not None) for t in todos],
            [("Open one", 1, False), ("Done one", 2, True)],
        )
        self.assertEqual(new.get_model("adventures", "Tick").objects.get().climber_id, climber.pk)
        self.assertEqual(new.get_model("adventures", "Weekend").objects.get().focus, "")
        names = set(new.get_model("adventures", "Area").objects.values_list("name", flat=True))
        self.assertNotIn("Arizona Snowbowl", names)
        self.assertIn("Sedona", names)
