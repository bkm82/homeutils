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
from .models import Climber, Crag, Credit, Focus, ForecastCache, Tick, ToDo, Trip, Weekend, Zone
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


RR = "bea6bf11-de53-5046-a5b4-b89217b7e9bc"
JUNIPER = "3a44c556-7981-5855-8c45-d5eb8f7c5274"
CLOUD_TOWER = "11111111-1111-1111-1111-111111111111"
CRIMSON = "28e8f602-f986-546c-917c-4d3d6ac0a2b4"
CLIMB_INDEX = [
    {
        "uuid": CRIMSON,
        "name": "Crimson Chrysalis",
        "grade": "5.8+",
        "crag_uuid": JUNIPER,
        "crag": "Juniper Canyon",
        "path": "Juniper Canyon › Cloud Tower",
    },
    {
        "uuid": "22222222-2222-2222-2222-222222222222",
        "name": "Chrysalis Direct",
        "grade": "5.10a",
        "crag_uuid": JUNIPER,
        "crag": "Juniper Canyon",
        "path": "Juniper Canyon",
    },
]


class ClimbSearchTests(TestCase):
    def test_region_climbs_tags_each_climb_with_its_crag(self):
        ancestors = ["usa", "nv", "snv", RR]
        responses = [
            {"area": {"ancestors": ancestors}},
            {
                "bulkAreas": [
                    {
                        "uuid": JUNIPER,
                        "area_name": "Juniper Canyon",
                        "ancestors": [*ancestors, JUNIPER],
                        "climbs": [],
                    },
                    {
                        "uuid": CLOUD_TOWER,
                        "area_name": "Cloud Tower",
                        "ancestors": [*ancestors, JUNIPER, CLOUD_TOWER],
                        "climbs": [
                            {
                                "uuid": CRIMSON,
                                "name": "Crimson Chrysalis",
                                "grades": {"yds": "5.8+"},
                            }
                        ],
                    },
                    {
                        "uuid": RR,
                        "area_name": "Red Rocks",
                        "ancestors": ancestors,
                        "climbs": [
                            {
                                "uuid": "b",
                                "name": "Boulder",
                                "grades": {"yds": None, "vscale": "V3"},
                            }
                        ],
                    },
                ]
            },
        ]
        with patch(OPENBETA_QUERY_PATH, side_effect=responses):
            climbs = openbeta.region_climbs(RR)
        self.assertEqual(
            [(c["name"], c["grade"], c["crag"], c["path"]) for c in climbs],
            [
                ("Crimson Chrysalis", "5.8+", "Juniper Canyon", "Juniper Canyon › Cloud Tower"),
                ("Boulder", "V3", "Red Rocks", ""),
            ],
        )
        self.assertEqual(climbs[1]["crag_uuid"], RR)

    def test_region_climbs_pages_through_bulk_areas(self):
        page = [
            {"uuid": f"a{i}", "area_name": "A", "ancestors": [RR], "climbs": []}
            for i in range(openbeta.BULK_PAGE)
        ]
        responses = [{"area": {"ancestors": [RR]}}, {"bulkAreas": page}, {"bulkAreas": page[:3]}]
        with patch(OPENBETA_QUERY_PATH, side_effect=responses) as query:
            openbeta.region_climbs(RR)
        self.assertEqual(query.call_args.args[1]["offset"], openbeta.BULK_PAGE)

    def test_search_prefers_prefix_matches(self):
        names = [c["name"] for c in openbeta.search_climbs(CLIMB_INDEX, "chrysalis")]
        self.assertEqual(names, ["Chrysalis Direct", "Crimson Chrysalis"])
        self.assertEqual(openbeta.search_climbs(CLIMB_INDEX, "  "), [])


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
        zone = Zone.objects.create(name="Somewhere", latitude=1, longitude=2)
        with patch(FETCH_PATH, return_value=[_daily(SATURDAY, [{}])]) as fetch:
            weather.get_forecasts([zone])
            weather.get_forecasts([zone])
        self.assertEqual(fetch.call_count, 1)

        zone.latitude = 3
        with patch(FETCH_PATH, return_value=[_daily(SATURDAY, [{}])]) as fetch:
            weather.get_forecasts([zone])
        self.assertEqual(fetch.call_count, 1)

    def test_fetch_failure_falls_back_to_stale_cache(self):
        zone = Zone.objects.create(name="Somewhere", latitude=1, longitude=2)
        ForecastCache.objects.create(
            zone=zone,
            latitude=1,
            longitude=2,
            fetched_at=date(2020, 1, 1).isoformat() + "T00:00Z",
            daily=_daily(SATURDAY, [{}]),
        )
        with patch(FETCH_PATH, side_effect=OSError("offline")):
            forecasts = weather.get_forecasts([zone])
        self.assertEqual(len(forecasts[zone.id]), 1)


def _todo(climber, crag, rank, **kwargs):
    return ToDo.objects.create(
        climber=climber, crag=crag, rank=rank, name=f"{climber}-{crag.name}-{rank}", **kwargs
    )


def _crag(zone, name, **kwargs):
    return Crag.objects.create(zone=zone, name=name, **kwargs)


class LedgerTests(TestCase):
    def setUp(self):
        zone = Zone.objects.create(name="Z", latitude=0, longitude=0)
        self.a = _crag(zone, "A")
        self.b = _crag(zone, "B")
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

    def test_two_days_at_one_crag_is_a_whole_weekend(self):
        values = {(self.me.pk, self.a.pk): 1.0}
        credits = ledger.split_credit([self.me.pk, self.you.pk], [self.a.pk, self.a.pk], values)
        self.assertEqual(credits, {self.me.pk: (1.0, 0.5), self.you.pk: (0.0, 0.5)})

    def test_each_day_credits_half_its_crag(self):
        values = {(self.me.pk, self.a.pk): 1.0, (self.you.pk, self.b.pk): 1.0}
        credits = ledger.split_credit([self.me.pk, self.you.pk], [self.a.pk, self.b.pk], values)
        self.assertEqual(credits, {self.me.pk: (0.5, 0.5), self.you.pk: (0.5, 0.5)})
        one_day = ledger.split_credit([self.me.pk], [self.a.pk], values)
        self.assertEqual(one_day, {self.me.pk: (0.5, 0.5)})

    def test_balances_and_need(self):
        weekend = Weekend.objects.create(saturday=SATURDAY)
        Credit.objects.create(weekend=weekend, climber=self.me, value=1.0, share=0.5)
        Credit.objects.create(weekend=weekend, climber=self.you, value=0.0, share=0.5)
        balances = ledger.balances(Credit.objects.all())
        self.assertEqual(balances, {self.me.pk: -0.5, self.you.pk: 0.5})
        self.assertEqual(ledger.need_weight(0.5), 1.5)
        self.assertEqual(ledger.need_weight(-5), ledger.MIN_NEED_WEIGHT)


RAIN = {"precip": 0.5, "precip_chance": 90, "code": 63}


class SelectorTests(TestCase):
    def setUp(self):
        Zone.objects.all().delete()  # drop the seeded starter zones
        self.flagstaff = Zone.objects.create(
            name="Flagstaff", latitude=1, longitude=1, is_local=True
        )
        self.sedona = Zone.objects.create(name="Sedona", latitude=2, longitude=2, is_local=True)
        self.red_rocks = Zone.objects.create(name="Red Rocks", latitude=3, longitude=3)
        self.zones = [self.flagstaff, self.sedona, self.red_rocks]
        self.peaks = _crag(self.flagstaff, "The Peaks")
        self.hobo = _crag(self.flagstaff, "Hobo Jungle")
        self.butler = _crag(self.sedona, "Mt. Butler", sandstone=True)
        self.calico = _crag(self.red_rocks, "Calico", sandstone=True)
        self.velvet = _crag(self.red_rocks, "Black Velvet", sandstone=True)
        self.me = Climber.objects.create(name="Me")
        self.you = Climber.objects.create(name="You")
        self.climbers = [self.me, self.you]
        self.forecasts = {z.id: self._forecast() for z in self.zones}

    def _forecast(self, saturday=None, sunday=None):
        start = SATURDAY - timedelta(days=3)
        return parse_daily(_daily(start, [{}, {}, {}, saturday or {}, sunday or {}]))

    def _weekend(self, attendees=None, balances=None, focus=Focus.NONE, levels=None, trips=()):
        return selectors.build_weekend(
            SATURDAY,
            Weekend(saturday=SATURDAY, focus=focus),
            self.climbers,
            self.climbers if attendees is None else attendees,
            self.zones,
            list(Crag.objects.select_related("zone")),
            self.forecasts,
            list(ToDo.objects.filter(sent_on__isnull=True)),
            balances or {},
            levels or {},
            list(trips),
        )

    def _picks(self, plan):
        return [p.crag for p in plan.picks]

    def test_weekend_saturdays(self):
        self.assertEqual(selectors.weekend_saturdays(date(2026, 11, 2))[0], SATURDAY)  # Mon
        self.assertEqual(selectors.weekend_saturdays(SATURDAY + timedelta(days=1))[0], SATURDAY)
        self.assertEqual(len(selectors.weekend_saturdays(date(2026, 11, 2))), 2)

    def test_one_crag_per_day_even_within_a_zone(self):
        _todo(self.me, self.peaks, 1, is_project=True)
        _todo(self.you, self.hobo, 1)
        top = self._weekend().plans[0]
        self.assertEqual(top.label, "Local")
        self.assertEqual(len(top.picks), 2)
        # Each day is one crag; the repeat discount spreads the two days.
        self.assertEqual(set(self._picks(top)), {self.peaks, self.hobo})

    def test_local_days_can_mix_local_zones(self):
        _todo(self.me, self.peaks, 1)
        _todo(self.you, self.butler, 1)
        top = self._weekend().plans[0]
        self.assertEqual(set(self._picks(top)), {self.peaks, self.butler})

    def test_a_clear_favourite_gets_both_days(self):
        _todo(self.me, self.peaks, 1, is_project=True)
        _todo(self.you, self.peaks, 1, is_project=True)
        _todo(self.you, self.hobo, 5)
        self.assertEqual(self._picks(self._weekend().plans[0]), [self.peaks, self.peaks])

    def test_trip_zone_plan_stays_in_its_zone(self):
        _todo(self.me, self.calico, 1)
        _todo(self.you, self.velvet, 1)
        self.forecasts[self.flagstaff.id] = self._forecast(RAIN, RAIN)
        self.forecasts[self.sedona.id] = self._forecast(RAIN, RAIN)
        top = self._weekend().plans[0]
        self.assertEqual((top.label, top.zone), ("Red Rocks", self.red_rocks))
        self.assertEqual(set(self._picks(top)), {self.calico, self.velvet})

    def test_whoever_is_owed_pulls_the_pick(self):
        _todo(self.me, self.peaks, 1)
        _todo(self.you, self.calico, 1)
        top = self._weekend(balances={self.you.pk: 0.5, self.me.pk: -0.5}).plans[0]
        self.assertEqual(top.zone, self.red_rocks)

    def test_only_attendees_count(self):
        _todo(self.me, self.peaks, 1)
        _todo(self.you, self.calico, 1)
        _todo(self.you, self.calico, 2)
        self.assertEqual(self._weekend(attendees=[self.me]).plans[0].label, "Local")

    def test_weather_picks_the_dry_day_crag(self):
        _todo(self.me, self.butler, 1)
        _todo(self.me, self.peaks, 2)
        # Rain in Sedona on Saturday only - wet sandstone rules Sunday out too.
        self.forecasts[self.sedona.id] = self._forecast(RAIN, {})
        top = self._weekend().plans[0]
        self.assertNotIn(self.butler, self._picks(top))

    def test_try_hard_counts_only_projects(self):
        _todo(self.me, self.peaks, 1)
        _todo(self.me, self.peaks, 2)
        _todo(self.you, self.calico, 2, is_project=True)
        self.assertEqual(self._weekend().plans[0].label, "Local")
        self.assertEqual(self._weekend(focus=Focus.TRY_HARD).plans[0].zone, self.red_rocks)

    def test_volume_focus_ranks_by_routes_in_attendees_bands(self):
        _todo(self.me, self.peaks, 1)
        Crag.objects.filter(pk=self.calico.pk).update(grade_counts={"5.10": 40, "5.12a": 5})
        Crag.objects.filter(pk=self.velvet.pk).update(grade_counts={"5.10a": 30})
        levels = {self.me.pk: ClimbingLevel(onsight=10.5, redpoint=11.5)}
        top = self._weekend(focus=Focus.VOLUME, levels=levels).plans[0]
        self.assertEqual(top.zone, self.red_rocks)
        self.assertEqual(top.picks[0].volume_count, 40)

    def test_rained_out_trip_suggests_pivots(self):
        self.forecasts[self.red_rocks.id] = self._forecast(RAIN, RAIN)
        trip = Trip.objects.create(
            title="Crew trip", zone=self.red_rocks, start_date=SATURDAY, end_date=SATURDAY
        )
        [outlook] = self._weekend(trips=[trip]).trips
        self.assertEqual(outlook.plan.verdict, "no")
        self.assertEqual([p.label for p in outlook.alternatives], ["Local"])


class ViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("me", password="pw")
        self.client.force_login(self.user)
        # Seeded: each starter zone holds one crag of the same name.
        self.peaks = Crag.objects.get(name="Flagstaff")
        self.sedona = Crag.objects.get(name="Sedona")
        self.me = Climber.objects.create(name="Me", user=self.user)
        self.you = Climber.objects.create(name="You")
        # Only weekends that have started can be logged.
        self.past = selectors.weekend_saturdays(timezone.localdate())[0] - timedelta(weeks=1)

    def _post_log(self, crag=None, **data):
        data.setdefault("outcome", "climbed")
        data.setdefault("attendees", [self.me.pk, self.you.pk])
        if crag:
            data.setdefault("saturday_crag", crag.pk)
            data.setdefault("sunday_crag", crag.pk)
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
        self._post_log(self.peaks, worked=[project.pk], sent=[project.pk])

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
        self._post_log(self.peaks, sent=[elsewhere.pk])
        elsewhere.refresh_from_db()
        self.assertIsNone(elsewhere.sent_on)

    def test_rained_out_weekend_credits_nobody(self):
        _todo(self.me, self.peaks, 1)
        self._post_log(outcome="rained_out")
        self.assertEqual(Weekend.objects.get().outcome, "rained_out")
        self.assertFalse(Credit.objects.exists())

    def test_cannot_log_twice_without_undo(self):
        _todo(self.me, self.peaks, 1)
        self._post_log(self.peaks)
        self.assertEqual(self._post_log(self.peaks).status_code, 400)
        self.client.post(reverse("adventures:undo_log", args=[self.past.isoformat()]))
        self.assertFalse(Credit.objects.exists())
        self._post_log(outcome="rained_out")
        self.assertEqual(Weekend.objects.get().outcome, "rained_out")

    def test_log_splits_credit_across_days(self):
        _todo(self.me, self.peaks, 1)
        _todo(self.you, self.sedona, 1)
        self._post_log(saturday_crag=self.peaks.pk, sunday_crag=self.sedona.pk)
        weekend = Weekend.objects.get()
        self.assertEqual(weekend.climbed_crags, [self.peaks, self.sedona])
        credits = {c.climber: (c.value, c.share) for c in Credit.objects.all()}
        self.assertEqual(credits, {self.me: (0.5, 0.5), self.you: (0.5, 0.5)})

    def test_one_day_weekend_counts_half(self):
        _todo(self.me, self.peaks, 1)
        self._post_log(saturday_crag=self.peaks.pk)
        credits = {c.climber: (c.value, c.share) for c in Credit.objects.all()}
        self.assertEqual(credits, {self.me: (0.5, 0.25), self.you: (0.0, 0.25)})

    def test_log_requires_a_crag_when_climbed(self):
        self._post_log()
        self.assertFalse(Weekend.objects.filter(outcome="climbed").exists())

    def test_cannot_log_a_future_weekend(self):
        future = selectors.weekend_saturdays(timezone.localdate())[-1] + timedelta(weeks=1)
        response = self.client.get(reverse("adventures:log", args=[future.isoformat()]))
        self.assertEqual(response.status_code, 400)

    def test_log_todos_partial(self):
        _todo(self.me, self.peaks, 1)
        url = reverse("adventures:log_todos", args=[SATURDAY.isoformat()])
        response = self.client.get(
            url, {"saturday_crag": self.peaks.pk, "attendees": [self.me.pk]}
        )
        self.assertContains(response, "Worked")

    def test_season_page(self):
        _todo(self.me, self.peaks, 1)
        self._post_log(self.peaks)
        response = self.client.get(reverse("adventures:season"))
        self.assertContains(response, "owed 0.5")
        self.assertContains(response, "ahead 0.5")

    def test_add_todo_appends_and_move_reorders(self):
        first = _todo(self.me, self.peaks, 1)
        self.client.post(
            reverse("adventures:todo_add", args=[self.me.pk]),
            {"name": "New", "crag": self.sedona.pk},
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
        self.assertEqual(Crag.objects.get(name="Sedona").grade_counts, {"5.10": 7})

    def test_refresh_route_counts_survives_openbeta_outage(self):
        with patch(OPENBETA_QUERY_PATH, side_effect=OSError("down")):
            response = self.client.post(reverse("adventures:refresh_routes"), follow=True)
        self.assertContains(response, "Couldn&#x27;t reach OpenBeta")

    def _openbeta_zone(self):
        return Zone.objects.create(
            name="Red Rocks", latitude=36, longitude=-115, sandstone=True, openbeta_uuids=[RR]
        )

    def test_climb_search_builds_and_reuses_the_zone_index(self):
        zone = self._openbeta_zone()
        url = reverse("adventures:climb_search", args=[self.me.pk])
        with patch("apps.adventures.openbeta.region_climbs", return_value=CLIMB_INDEX) as fetch:
            response = self.client.get(url, {"zone": zone.pk, "q": "crimson"})
            self.client.get(url, {"zone": zone.pk, "q": "direct"})
        self.assertContains(response, "Crimson Chrysalis")
        self.assertContains(response, "Red Rocks › Juniper Canyon › Cloud Tower")
        self.assertEqual(fetch.call_count, 1)

    def test_climb_search_survives_openbeta_outage(self):
        zone = self._openbeta_zone()
        with patch("apps.adventures.openbeta.region_climbs", side_effect=OSError("down")):
            response = self.client.get(
                reverse("adventures:climb_search", args=[self.me.pk]),
                {"zone": zone.pk, "q": "crimson"},
            )
        self.assertContains(response, "OpenBeta didn")

    def test_adding_a_climb_files_it_under_an_auto_created_crag(self):
        zone = self._openbeta_zone()
        zone.climb_index = CLIMB_INDEX
        zone.save()
        url = reverse("adventures:todo_from_openbeta", args=[self.me.pk])
        self.client.post(url, {"zone": zone.pk, "climb": CRIMSON})
        todo = ToDo.objects.get()
        self.assertEqual(
            (todo.name, todo.grade, todo.openbeta_uuid), ("Crimson Chrysalis", "5.8+", CRIMSON)
        )
        self.assertEqual(
            (todo.crag.zone, todo.crag.name, todo.crag.sandstone), (zone, "Juniper Canyon", True)
        )
        self.assertEqual(todo.crag.openbeta_uuids, [JUNIPER])

        # A second climb there reuses the crag; the same climb isn't added twice.
        self.client.post(url, {"zone": zone.pk, "climb": CLIMB_INDEX[1]["uuid"]})
        self.client.post(url, {"zone": zone.pk, "climb": CRIMSON})
        self.assertEqual(ToDo.objects.count(), 2)
        self.assertEqual(zone.crags.count(), 1)

    def test_adding_a_climb_links_a_same_named_crag(self):
        zone = self._openbeta_zone()
        zone.climb_index = CLIMB_INDEX
        zone.save()
        existing = _crag(zone, "juniper canyon")
        self.client.post(
            reverse("adventures:todo_from_openbeta", args=[self.me.pk]),
            {"zone": zone.pk, "climb": CRIMSON},
        )
        existing.refresh_from_db()
        self.assertEqual(ToDo.objects.get().crag, existing)
        self.assertEqual(existing.openbeta_uuids, [JUNIPER])

    def test_unknown_climb_is_rejected(self):
        zone = self._openbeta_zone()
        response = self.client.post(
            reverse("adventures:todo_from_openbeta", args=[self.me.pk]),
            {"zone": zone.pk, "climb": CRIMSON},
        )
        self.assertEqual(response.status_code, 400)

    def test_zone_from_openbeta_creates_or_reuses(self):
        Zone.objects.update(openbeta_uuids=[])  # the seeded Red Rock zone is linked to RR
        info = {"uuid": RR, "name": "Red Rocks", "latitude": 36.1, "longitude": -115.4}
        url = reverse("adventures:zone_from_openbeta")
        next_url = reverse("adventures:todo_add", args=[self.me.pk])
        with patch("apps.adventures.openbeta.area_info", return_value=info):
            response = self.client.post(url, {"uuid": RR, "next": next_url})
        zone = Zone.objects.get(openbeta_uuids=[RR])
        self.assertEqual((zone.name, zone.latitude, zone.is_local), ("Red Rocks", 36.1, False))
        self.assertRedirects(response, f"{next_url}?zone={zone.pk}", fetch_redirect_response=False)
        with patch("apps.adventures.openbeta.area_info") as info_call:
            self.client.post(url, {"uuid": RR})
        info_call.assert_not_called()
        self.assertEqual(Zone.objects.filter(openbeta_uuids=[RR]).count(), 1)

    def test_zone_from_openbeta_avoids_name_clashes(self):
        Zone.objects.update(openbeta_uuids=[])
        info = {"uuid": RR, "name": "Sedona", "latitude": 1, "longitude": 2}
        with patch("apps.adventures.openbeta.area_info", return_value=info):
            self.client.post(
                reverse("adventures:zone_from_openbeta"), {"uuid": RR, "path": "Somewhere"}
            )
        self.assertTrue(Zone.objects.filter(name="Sedona (Somewhere)").exists())

    def test_todo_add_page_offers_openbeta_zones(self):
        zone = self._openbeta_zone()
        response = self.client.get(
            reverse("adventures:todo_add", args=[self.me.pk]), {"zone": zone.pk}
        )
        self.assertContains(response, f'<option value="{zone.pk}" selected>Red Rocks</option>')

    def test_crag_form_rejects_bad_openbeta_id(self):
        response = self.client.post(
            reverse("adventures:crag_add"),
            {"zone": self.sedona.zone_id, "name": "X", "openbeta_uuids": "not-a-uuid"},
        )
        self.assertContains(response, "Not an OpenBeta area id")

    def test_add_zone_and_crags_from_the_ui(self):
        self.client.post(
            reverse("adventures:zone_add"),
            {"name": "Joshua Tree", "latitude": "34.0", "longitude": "-116.2"},
        )
        zone = Zone.objects.get(name="Joshua Tree")
        self.assertFalse(zone.is_local)
        page = self.client.get(reverse("adventures:crag_add"), {"zone": zone.pk})
        self.assertContains(page, f'<option value="{zone.pk}" selected>')
        self.client.post(reverse("adventures:crag_add"), {"zone": zone.pk, "name": "Echo Cove"})
        self.client.post(
            reverse("adventures:crag_add"), {"zone": zone.pk, "name": "Hidden Valley"}
        )
        self.assertEqual(zone.crags.count(), 2)
        self.assertContains(self.client.get(reverse("adventures:zones")), "Hidden Valley")

    def test_crag_names_are_unique_within_a_zone_only(self):
        other = Zone.objects.create(name="Elsewhere", latitude=0, longitude=0)
        self.client.post(reverse("adventures:crag_add"), {"zone": other.pk, "name": "Sedona"})
        self.assertTrue(other.crags.filter(name="Sedona").exists())
        response = self.client.post(
            reverse("adventures:crag_add"), {"zone": other.pk, "name": "Sedona"}
        )
        self.assertEqual(response.status_code, 200)  # re-rendered with an error
        self.assertEqual(other.crags.count(), 1)

    def test_delete_todo_renumbers(self):
        first = _todo(self.me, self.peaks, 1)
        second = _todo(self.me, self.peaks, 2)
        self.client.post(reverse("adventures:delete", args=["todo", first.pk]))
        second.refresh_from_db()
        self.assertEqual(second.rank, 1)


class MigrationTestCase(TransactionTestCase):
    """Roll the schema back to `before`, seed old-shaped rows, then migrate to `after`."""

    before: list[tuple[str, str]]
    after: list[tuple[str, str]]

    def old_apps(self):
        # Later migrations drop NOT NULL columns, which can't be re-added on
        # rollback while rows exist - so start from empty crag/zone tables.
        Zone.objects.all().delete()
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        return executor.loader.project_state(self.before).apps

    def migrate_to_after(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        return executor.loader.project_state(self.after).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


class GroupMigrationTests(MigrationTestCase):
    """0003/0004 carry v0.7.0's per-user goals and ticks over to climber profiles."""

    before = [("adventures", "0002_seed_areas")]
    after = [("adventures", "0004_drop_non_climbing")]

    def test_goals_and_ticks_become_climber_data(self):
        old = self.old_apps()
        user = old.get_model("auth", "User").objects.create(username="pat", first_name="Pat")
        OldArea = old.get_model("adventures", "Area")
        coords = {"latitude": 0, "longitude": 0}
        area = OldArea.objects.create(name="Sedona", activities=["climbing"], **coords)
        OldArea.objects.create(name="Arizona Snowbowl", activities=["skiing"], **coords)
        Goal = old.get_model("adventures", "Goal")
        Goal.objects.create(owner_id=user.pk, name="Done one", area=area, is_done=True)
        Goal.objects.create(owner_id=user.pk, name="Open one", area=area)
        Goal.objects.create(owner_id=user.pk, name="Ski", area=area, activity="skiing")
        old.get_model("adventures", "Tick").objects.create(
            owner_id=user.pk, date=SATURDAY, route="R"
        )
        old.get_model("adventures", "Weekend").objects.create(saturday=SATURDAY, focus="rest")

        new = self.migrate_to_after()
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


class ZoneMigrationTests(MigrationTestCase):
    """0006/0007 turn each area into a zone holding one crag of the same name."""

    before = [("adventures", "0005_climber_grades")]
    after = [("adventures", "0007_drop_area_fields")]

    def test_areas_become_zones_with_one_crag(self):
        old = self.old_apps()
        Area = old.get_model("adventures", "Area")
        sedona = Area.objects.create(
            name="Sedona", latitude=34.8, longitude=-111.7, sandstone=True
        )
        moab = Area.objects.create(name="Moab", latitude=38.5, longitude=-109.5)
        climber = old.get_model("adventures", "Climber").objects.create(name="Pat")
        old.get_model("adventures", "ToDo").objects.create(climber=climber, name="R", area=sedona)
        old.get_model("adventures", "Trip").objects.create(
            title="Crew", area=moab, start_date=SATURDAY, end_date=SATURDAY
        )
        old.get_model("adventures", "Weekend").objects.create(
            saturday=SATURDAY, outcome="climbed", area=sedona
        )

        new = self.migrate_to_after()
        zones = {z.name: z for z in new.get_model("adventures", "Zone").objects.all()}
        self.assertEqual(set(zones), {"Sedona", "Moab"})
        self.assertTrue(zones["Sedona"].is_local)
        self.assertFalse(zones["Moab"].is_local)
        self.assertEqual(zones["Sedona"].latitude, 34.8)
        crag = new.get_model("adventures", "Crag").objects.get(name="Sedona")
        self.assertEqual((crag.zone_id, crag.sandstone), (zones["Sedona"].pk, True))
        self.assertEqual(new.get_model("adventures", "ToDo").objects.get().crag_id, crag.pk)
        self.assertEqual(
            new.get_model("adventures", "Trip").objects.get().zone_id, zones["Moab"].pk
        )
        weekend = new.get_model("adventures", "Weekend").objects.get()
        self.assertEqual((weekend.saturday_crag_id, weekend.sunday_crag_id), (crag.pk, crag.pk))


class ZoneLinkMigrationTests(MigrationTestCase):
    """0008 copies each placeholder crag's OpenBeta links and rock type up to its zone."""

    before = [("adventures", "0007_drop_area_fields")]
    after = [("adventures", "0008_openbeta_climbs")]

    def test_zone_inherits_placeholder_crag_links(self):
        old = self.old_apps()
        Zone_ = old.get_model("adventures", "Zone")
        Crag_ = old.get_model("adventures", "Crag")
        zone = Zone_.objects.create(name="Red Rock", latitude=0, longitude=0)
        Crag_.objects.create(zone=zone, name="Red Rock", sandstone=True, openbeta_uuids=[RR])
        Crag_.objects.create(zone=zone, name="Calico", openbeta_uuids=[JUNIPER])

        new = self.migrate_to_after()
        zone = new.get_model("adventures", "Zone").objects.get()
        self.assertEqual((zone.openbeta_uuids, zone.sandstone), ([RR], True))
