from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import conditions, grades, openbeta, selectors, weather
from .grades import ClimbingLevel
from .models import Activity, Area, Focus, ForecastCache, Goal, Tick, Trip, Weekend
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
    def test_dry_mild_day_is_go_for_climbing(self):
        rating = conditions.rate(Activity.CLIMBING, _day(SATURDAY), [])
        self.assertEqual(rating.verdict, "go")

    def test_rain_rules_out_climbing_but_hiking_is_still_marginal(self):
        wet = _day(SATURDAY, precip=0.4, precip_chance=80, code=63)
        self.assertEqual(conditions.rate(Activity.CLIMBING, wet, []).verdict, "no")
        self.assertEqual(conditions.rate(Activity.HIKING, wet, []).verdict, "marginal")

    def test_recent_rain_rules_out_sandstone_only(self):
        prior = [_day(SATURDAY - timedelta(days=1), precip=0.3)]
        dry = _day(SATURDAY)
        self.assertEqual(
            conditions.rate(Activity.CLIMBING, dry, prior, sandstone=True).verdict, "no"
        )
        self.assertEqual(conditions.rate(Activity.CLIMBING, dry, prior).verdict, "go")

    def test_cold_day_is_poor_for_climbing(self):
        rating = conditions.rate(Activity.CLIMBING, _day(SATURDAY, high=33), [])
        self.assertEqual(rating.verdict, "no")

    def test_fresh_snow_makes_skiing_go(self):
        prior = [_day(SATURDAY - timedelta(days=1), snowfall=8, high=28, code=73)]
        rating = conditions.rate(Activity.SKIING, _day(SATURDAY, high=30), prior)
        self.assertEqual(rating.verdict, "go")

    def test_warm_dry_day_is_no_for_skiing(self):
        self.assertEqual(conditions.rate(Activity.SKIING, _day(SATURDAY), []).verdict, "no")

    def test_ice_needs_a_sustained_freeze(self):
        cold = [_day(SATURDAY - timedelta(days=n), high=25) for n in (3, 2, 1)]
        self.assertEqual(
            conditions.rate(Activity.ICE, _day(SATURDAY, high=28), cold).verdict, "go"
        )
        self.assertEqual(
            conditions.rate(Activity.ICE, _day(SATURDAY, high=45), cold).verdict, "no"
        )


class WeatherTests(TestCase):
    def test_parse_skips_null_padded_days(self):
        daily = _daily(SATURDAY, [{}, {}])
        daily["temperature_2m_max"][1] = None
        self.assertEqual([d.date for d in parse_daily(daily)], [SATURDAY])

    def test_get_forecasts_uses_cache_until_stale(self):
        area = Area.objects.create(name="Crag", latitude=1, longitude=2, activities=["climbing"])
        with patch(FETCH_PATH, return_value=[_daily(SATURDAY, [{}])]) as fetch:
            weather.get_forecasts([area])
            weather.get_forecasts([area])
        self.assertEqual(fetch.call_count, 1)

        area.latitude = 3
        with patch(FETCH_PATH, return_value=[_daily(SATURDAY, [{}])]) as fetch:
            weather.get_forecasts([area])
        self.assertEqual(fetch.call_count, 1)

    def test_fetch_failure_falls_back_to_stale_cache(self):
        area = Area.objects.create(name="Crag", latitude=1, longitude=2, activities=["climbing"])
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


class SelectorTests(TestCase):
    def setUp(self):
        Area.objects.all().delete()  # drop the seeded starter areas
        self.me = User.objects.create_user("me", password="pw")
        self.partner = User.objects.create_user("partner", password="pw")
        self.dry = Area.objects.create(
            name="Dry crag", latitude=1, longitude=1, activities=["climbing", "hiking"]
        )
        self.wet = Area.objects.create(
            name="Wet crag", latitude=2, longitude=2, activities=["climbing", "hiking"]
        )
        self.areas = [self.dry, self.wet]
        rain = {"precip": 0.5, "precip_chance": 90, "code": 63}
        start = SATURDAY - timedelta(days=3)
        self.forecasts = {
            self.dry.id: parse_daily(_daily(start, [{}] * 5)),
            self.wet.id: parse_daily(_daily(start, [{}, {}, {}, rain, rain])),
        }

    def _weekend(self, priority=None, goals=(), trips=(), **kwargs):
        return selectors.build_weekend(
            SATURDAY, priority, self.areas, self.forecasts, list(goals), list(trips), **kwargs
        )

    def test_weekend_saturdays(self):
        self.assertEqual(selectors.weekend_saturdays(date(2026, 11, 2))[0], SATURDAY)  # Mon
        self.assertEqual(selectors.weekend_saturdays(SATURDAY)[0], SATURDAY)
        self.assertEqual(selectors.weekend_saturdays(SATURDAY + timedelta(days=1))[0], SATURDAY)
        self.assertEqual(len(selectors.weekend_saturdays(date(2026, 11, 2))), 2)

    def test_dry_climbing_ranks_first(self):
        top = self._weekend().options[0]
        self.assertEqual((top.area, top.activity), (self.dry, "climbing"))

    def test_priority_goal_breaks_ties_between_go_options(self):
        goal = Goal.objects.create(
            owner=self.partner, name="Hard thing", area=self.dry, activity="hiking"
        )
        top = self._weekend(priority=self.partner, goals=[goal]).options[0]
        self.assertEqual(top.activity, "hiking")
        self.assertEqual(top.priority_goals, [goal])

    def test_priority_goal_does_not_lift_a_no_go_option(self):
        goal = Goal.objects.create(owner=self.me, name="Wet project", area=self.wet)
        top = self._weekend(priority=self.me, goals=[goal]).options[0]
        self.assertEqual(top.area, self.dry)

    def test_rained_out_trip_suggests_pivots(self):
        trip = Trip.objects.create(
            title="Crew trip",
            area=self.wet,
            activity="climbing",
            start_date=SATURDAY,
            end_date=SATURDAY + timedelta(days=1),
        )
        [outlook] = self._weekend(trips=[trip]).trips
        self.assertEqual(outlook.option.verdict, "no")
        self.assertIn((self.dry, "climbing"), [(o.area, o.activity) for o in outlook.alternatives])

    def _second_dry_crag(self, **kwargs):
        crag = Area.objects.create(
            name="Other dry crag", latitude=3, longitude=3, activities=["climbing"], **kwargs
        )
        self.areas.append(crag)
        self.forecasts[crag.id] = self.forecasts[self.dry.id]
        return crag

    def test_volume_focus_ranks_by_routes_in_onsight_band(self):
        self.dry.grade_counts = {"5.10a": 3, "5.12a": 200}
        busy = self._second_dry_crag(grade_counts={"5.10": 40, "5.10a": 10})
        level = ClimbingLevel(onsight=10.5, redpoint=11.75)
        weekend = self._weekend(focus=Focus.VOLUME, climber=self.me, level=level)
        top = weekend.options[0]
        self.assertEqual((top.area, top.volume_count), (busy, 50))
        dry = next(o for o in weekend.options if o.area == self.dry and o.activity == "climbing")
        self.assertEqual(dry.volume_count, 3)

    def test_try_hard_focus_prefers_climbers_projects(self):
        project_crag = self._second_dry_crag()
        Goal.objects.create(owner=self.partner, name="Not mine", area=self.dry)
        mine = Goal.objects.create(owner=self.me, name="Project", area=project_crag)
        top = self._weekend(
            goals=list(Goal.objects.all()), focus=Focus.TRY_HARD, climber=self.me
        ).options[0]
        self.assertEqual(top.area, project_crag)
        self.assertEqual(top.goals, [mine])

    def test_rest_focus_puts_hiking_first(self):
        top = self._weekend(focus=Focus.REST).options[0]
        self.assertEqual((top.area, top.activity), (self.dry, "hiking"))

    def test_focus_never_outranks_weather(self):
        self.wet.grade_counts = {"5.10": 500}
        level = ClimbingLevel(onsight=10.5, redpoint=None)
        top = self._weekend(focus=Focus.VOLUME, climber=self.me, level=level).options[0]
        self.assertEqual(top.area, self.dry)

    def test_trip_outside_forecast_has_no_rating(self):
        trip = Trip.objects.create(
            title="Far off",
            area=self.dry,
            start_date=SATURDAY - timedelta(days=30),
            end_date=SATURDAY + timedelta(days=30),
        )
        self.forecasts = {}
        [outlook] = self._weekend(trips=[trip]).trips
        self.assertIsNone(outlook.option)


class ViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("me", password="pw")
        self.client.force_login(self.user)
        self.area = Area.objects.get(name="Sedona")

    def test_planner_renders_with_api_down(self):
        with patch(FETCH_PATH, side_effect=OSError("offline")):
            response = self.client.get(reverse("adventures:planner"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No forecast available")

    def test_planner_renders_forecast(self):
        def fake_fetch(points):
            return [_daily(timezone.localdate() - timedelta(days=3), [{}] * 19) for _ in points]

        with patch(FETCH_PATH, side_effect=fake_fetch):
            response = self.client.get(reverse("adventures:planner"))
        self.assertContains(response, "Climbing · Sedona")

    def test_set_priority(self):
        url = reverse("adventures:weekend", args=[SATURDAY.isoformat()])
        self.client.post(url, {"person": self.user.pk})
        self.assertEqual(Weekend.objects.get(saturday=SATURDAY).priority, self.user)
        self.client.post(url, {"person": ""})
        self.assertIsNone(Weekend.objects.get(saturday=SATURDAY).priority)

    def test_set_priority_rejects_non_saturday(self):
        url = reverse("adventures:weekend", args=[(SATURDAY + timedelta(days=1)).isoformat()])
        self.assertEqual(self.client.post(url, {"person": ""}).status_code, 400)

    def test_set_focus_keeps_priority(self):
        url = reverse("adventures:weekend", args=[SATURDAY.isoformat()])
        self.client.post(url, {"person": self.user.pk})
        self.client.post(url, {"focus": "volume"})
        weekend = Weekend.objects.get(saturday=SATURDAY)
        self.assertEqual((weekend.priority, weekend.focus), (self.user, "volume"))
        self.assertEqual(self.client.post(url, {"focus": "bogus"}).status_code, 400)

    def test_planner_volume_note_uses_imported_level(self):
        Weekend.objects.create(
            saturday=selectors.weekend_saturdays(timezone.localdate())[0], focus="volume"
        )
        Tick.objects.create(
            owner=self.user,
            date=timezone.localdate(),
            route="R",
            rating="5.10c",
            grade=10.5,
            lead_style="Onsight",
        )
        with patch(FETCH_PATH, side_effect=OSError("offline")):
            response = self.client.get(reverse("adventures:planner"))
        self.assertContains(response, "onsight 5.10c")

    def test_tick_import_replaces_previous_and_shows_level(self):
        Tick.objects.create(owner=self.user, date=SATURDAY, route="Old", rating="5.8")
        upload = SimpleUploadedFile("ticks.csv", TICK_CSV.encode(), content_type="text/csv")
        self.client.post(reverse("adventures:ticks"), {"file": upload})
        self.assertEqual(
            sorted(Tick.objects.values_list("route", flat=True)), ["Boulder", "Crack", "Slab"]
        )
        response = self.client.get(reverse("adventures:ticks"))
        self.assertContains(response, "5.10c onsight")

    def test_tick_import_rejects_non_export(self):
        upload = SimpleUploadedFile("x.csv", b"foo,bar\n1,2\n", content_type="text/csv")
        response = self.client.post(reverse("adventures:ticks"), {"file": upload})
        self.assertContains(response, "Expected a Mountain Project tick export")

    def test_refresh_route_counts(self):
        response_data = {"a0": {"aggregate": {"byGrade": [{"label": "5.10", "count": 7}]}}}
        with patch(OPENBETA_QUERY_PATH, return_value=response_data):
            self.client.post(reverse("adventures:refresh_routes"))
        self.assertEqual(Area.objects.get(name="Sedona").grade_counts, {"5.10": 7})
        self.assertIsNone(Area.objects.get(name="Arizona Snowbowl").grade_counts_updated_at)

    def test_refresh_route_counts_survives_openbeta_outage(self):
        with patch(OPENBETA_QUERY_PATH, side_effect=OSError("down")):
            response = self.client.post(reverse("adventures:refresh_routes"), follow=True)
        self.assertContains(response, "Couldn&#x27;t reach OpenBeta")

    def test_openbeta_search(self):
        data = {
            "areas": [
                {
                    "uuid": "u",
                    "area_name": "Indian Creek",
                    "pathTokens": ["USA", "Utah", "Indian Creek"],
                    "totalClimbs": 1502,
                }
            ]
        }
        with patch(OPENBETA_QUERY_PATH, return_value=data):
            response = self.client.get(reverse("adventures:openbeta_search"), {"q": "indian"})
        self.assertContains(response, "1502 climbs")

    def test_area_form_rejects_bad_openbeta_id(self):
        response = self.client.post(
            reverse("adventures:area_add"),
            {
                "name": "X",
                "latitude": "1",
                "longitude": "2",
                "activities": ["climbing"],
                "openbeta_uuids": "not-a-uuid",
            },
        )
        self.assertContains(response, "Not an OpenBeta area id")

    def test_add_goal_and_toggle(self):
        self.client.post(
            reverse("adventures:goal_add"),
            {"name": "Line", "area": self.area.pk, "activity": "climbing", "owner": self.user.pk},
        )
        goal = Goal.objects.get(name="Line")
        self.client.post(reverse("adventures:goal_toggle", args=[goal.pk]))
        goal.refresh_from_db()
        self.assertTrue(goal.is_done)

    def test_trip_end_before_start_is_rejected(self):
        response = self.client.post(
            reverse("adventures:trip_add"),
            {
                "title": "Backwards",
                "area": self.area.pk,
                "activity": "climbing",
                "start_date": "2026-11-08",
                "end_date": "2026-11-07",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Trip.objects.exists())

    def test_area_form_saves_activities(self):
        self.client.post(
            reverse("adventures:area_add"),
            {"name": "New", "latitude": "1.5", "longitude": "2.5", "activities": ["ice"]},
        )
        self.assertEqual(Area.objects.get(name="New").activities, ["ice"])

    def test_delete_trip(self):
        trip = Trip.objects.create(
            title="T", area=self.area, start_date=SATURDAY, end_date=SATURDAY
        )
        self.client.post(reverse("adventures:delete", args=["trip", trip.pk]))
        self.assertFalse(Trip.objects.exists())
