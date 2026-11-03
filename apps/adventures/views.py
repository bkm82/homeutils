import logging
from collections import defaultdict
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Max, Model
from django.forms import ModelForm
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import grades, ledger, openbeta, selectors
from .forms import ClimberForm, CragForm, TickImportForm, ToDoForm, TripForm, ZoneForm
from .models import Climber, Crag, Credit, Focus, Tick, ToDo, Trip, Weekend, Zone

logger = logging.getLogger(__name__)

# How far back the season page offers unlogged weekends for logging.
LOG_BACK_WEEKS = 8
# How long a zone's OpenBeta climb list is reused before refetching.
CLIMB_INDEX_TTL = timedelta(days=30)


def _saturday(value: str) -> date:
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise Http404("Invalid date") from None
    if day.weekday() != 5:
        raise Http404("Not a Saturday")
    return day


def _weekend(day: date) -> Weekend:
    """The Weekend row for `day`, created with everyone active attending."""
    weekend, created = Weekend.objects.get_or_create(saturday=day)
    if created:
        weekend.attendees.set(Climber.objects.filter(is_active=True))
    return weekend


@login_required
def planner(request: HttpRequest) -> HttpResponse:
    today = timezone.localdate()
    saturdays = selectors.weekend_saturdays(today)
    weekends = selectors.build_weekends(saturdays, today)
    last_sunday = weekends[-1].sunday if weekends else today
    later_trips = Trip.objects.filter(start_date__gt=last_sunday).select_related("zone")
    return render(
        request,
        "adventures/planner.html",
        {
            "weekends": weekends,
            "later_trips": later_trips,
            "focuses": Focus.choices,
            "today": today,
            "has_crags": Crag.objects.exists(),
            "has_climbers": Climber.objects.filter(is_active=True).exists(),
            "updated_at": selectors.forecast_updated_at(),
        },
    )


@login_required
@require_POST
def update_weekend(request: HttpRequest, saturday: str) -> HttpResponse:
    """Toggle someone in/out of a weekend and/or set its focus - whichever was posted."""
    day = _saturday(saturday)
    weekend = _weekend(day)
    if "climber" in request.POST:
        climber = get_object_or_404(Climber, pk=request.POST["climber"], is_active=True)
        if weekend.attendees.filter(pk=climber.pk).exists():
            weekend.attendees.remove(climber)
        else:
            weekend.attendees.add(climber)
    if "focus" in request.POST:
        if request.POST["focus"] not in Focus.values:
            return HttpResponseBadRequest("Unknown focus")
        weekend.focus = request.POST["focus"]
        weekend.save(update_fields=["focus"])

    if request.htmx:
        today = timezone.localdate()
        [outlook] = selectors.build_weekends([day], today)
        return render(
            request,
            "adventures/partials/weekend.html",
            {"weekend": outlook, "focuses": Focus.choices, "today": today},
        )
    return redirect("adventures:planner")


def _renumber(climber_id: int) -> None:
    """Keep open to-do ranks as 1..n so a rank is always a list position."""
    open_todos = ToDo.objects.filter(climber_id=climber_id, sent_on__isnull=True)
    for rank, todo in enumerate(open_todos.order_by("rank", "pk"), start=1):
        if todo.rank != rank:
            todo.rank = rank
            todo.save(update_fields=["rank"])


def _log_todos(attendee_ids: list[int], crag_ids: list[int]) -> list[ToDo]:
    if not crag_ids:
        return []
    return list(
        ToDo.objects.filter(
            climber_id__in=attendee_ids, crag_id__in=crag_ids, sent_on__isnull=True
        ).select_related("climber", "crag__zone")
    )


def _ids(values: list[str]) -> list[int]:
    return [int(v) for v in values if v.isdigit()]


@login_required
def log_weekend(request: HttpRequest, saturday: str) -> HttpResponse:
    day = _saturday(saturday)
    if day > timezone.localdate():
        return HttpResponseBadRequest("This weekend hasn't started yet")
    weekend = Weekend.objects.filter(saturday=day).first()
    climbers = Climber.objects.filter(is_active=True)

    if request.method == "POST":
        if weekend and weekend.outcome:
            return HttpResponseBadRequest("Already logged - undo it first")
        outcome = request.POST.get("outcome")
        attendees = list(Climber.objects.filter(pk__in=_ids(request.POST.getlist("attendees"))))
        day_crags = [
            Crag.objects.filter(pk=request.POST.get(f"{name}_crag") or None).first()
            for name in ("saturday", "sunday")
        ]
        error = None
        if outcome not in (Weekend.Outcome.CLIMBED, Weekend.Outcome.RAINED_OUT):
            error = "Pick what happened."
        elif outcome == Weekend.Outcome.CLIMBED and (not any(day_crags) or not attendees):
            error = "Pick where you climbed (at least one day) and who came."
        if not error:
            _apply_log(day, outcome, day_crags, attendees, request.POST)
            messages.success(request, f"Logged the weekend of {day:%b %-d}.")
            return redirect("adventures:season")
        messages.error(request, error)

    attendees = list(weekend.attendees.all()) if weekend else list(climbers)
    return render(
        request,
        "adventures/log_weekend.html",
        {
            "saturday": day,
            "weekend": weekend,
            "climbers": climbers,
            "attendee_ids": {c.pk for c in attendees},
            "crags": Crag.objects.select_related("zone").order_by("zone__name", "name"),
            "day_fields": [("saturday", "Saturday"), ("sunday", "Sunday")],
            "credits": weekend.credits.select_related("climber") if weekend else [],
        },
    )


@transaction.atomic
def _apply_log(
    day: date, outcome: str, day_crags: list[Crag | None], attendees: list[Climber], post
):
    weekend = _weekend(day)
    weekend.attendees.set(attendees)
    weekend.outcome = outcome
    climbed = outcome == Weekend.Outcome.CLIMBED
    weekend.saturday_crag, weekend.sunday_crag = day_crags if climbed else (None, None)
    weekend.save(update_fields=["outcome", "saturday_crag", "sunday_crag"])
    weekend.credits.all().delete()
    crag_ids = [c.pk for c in weekend.climbed_crags]
    if not crag_ids:
        return

    # Credit is worked out from the lists as they stood going in, before
    # this weekend's sends come off them.
    values = ledger.crag_values(ToDo.objects.filter(sent_on__isnull=True))
    attendee_ids = [c.pk for c in attendees]
    Credit.objects.bulk_create(
        Credit(weekend=weekend, climber_id=cid, value=value, share=share)
        for cid, (value, share) in ledger.split_credit(attendee_ids, crag_ids, values).items()
    )
    eligible = {t.pk: t for t in _log_todos(attendee_ids, crag_ids)}
    worked = set(_ids(post.getlist("worked")))
    sent = set(_ids(post.getlist("sent")))
    for pk in (worked | sent) & eligible.keys():
        todo = eligible[pk]
        todo.sessions += 1
        if pk in sent:
            todo.sent_on = day + timedelta(days=1)
        todo.save(update_fields=["sessions", "sent_on"])
    for climber_id in {eligible[pk].climber_id for pk in sent & eligible.keys()}:
        _renumber(climber_id)


@login_required
def log_todos(request: HttpRequest, saturday: str) -> HttpResponse:
    _saturday(saturday)
    crag_ids = _ids([request.GET.get("saturday_crag", ""), request.GET.get("sunday_crag", "")])
    todos = _log_todos(_ids(request.GET.getlist("attendees")), crag_ids)
    return render(request, "adventures/partials/log_todos.html", {"todos": todos})


@login_required
@require_POST
def undo_log(request: HttpRequest, saturday: str) -> HttpResponse:
    weekend = get_object_or_404(Weekend, saturday=_saturday(saturday))
    with transaction.atomic():
        weekend.credits.all().delete()
        weekend.outcome = Weekend.Outcome.PLANNED
        weekend.saturday_crag = weekend.sunday_crag = None
        weekend.save(update_fields=["outcome", "saturday_crag", "sunday_crag"])
    messages.success(
        request, "Log undone. Sessions and sends recorded with it stay - edit those to-dos."
    )
    return redirect("adventures:log", saturday=saturday)


@login_required
def season(request: HttpRequest) -> HttpResponse:
    balances = selectors.season_balances()
    totals: dict[int, dict] = defaultdict(lambda: {"weekends": 0, "value": 0.0})
    for credit in Credit.objects.all():
        totals[credit.climber_id]["weekends"] += 1
        totals[credit.climber_id]["value"] += credit.value
    rows = sorted(
        (
            {"climber": c, "balance": balances.get(c.pk, 0.0), **totals[c.pk]}
            for c in Climber.objects.filter(is_active=True)
        ),
        key=lambda r: r["balance"],
        reverse=True,
    )

    today = timezone.localdate()
    logged = Weekend.objects.exclude(outcome="").select_related(
        "saturday_crag__zone", "sunday_crag__zone"
    )
    logged = logged.prefetch_related("credits__climber").order_by("-saturday")
    logged_days = {w.saturday for w in logged}
    last_saturday = today - timedelta(days=(today.weekday() - 5) % 7)
    unlogged = [
        last_saturday - timedelta(weeks=n)
        for n in range(LOG_BACK_WEEKS)
        if last_saturday - timedelta(weeks=n) not in logged_days
    ]
    return render(
        request,
        "adventures/season.html",
        {"rows": rows, "logged": logged, "unlogged": unlogged},
    )


def _edit(
    request: HttpRequest,
    form_class: type[ModelForm],
    instance: Model | None,
    *,
    noun: str,
    success_url: str,
    delete_url: str | None = None,
    initial: dict | None = None,
    template: str = "adventures/form.html",
    on_create=None,
) -> HttpResponse:
    if request.method == "POST":
        form = form_class(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            if instance is None and on_create:
                on_create(obj)
            obj.save()
            return redirect(success_url)
    else:
        form = form_class(instance=instance, initial=initial)
    return render(
        request,
        template,
        {"form": form, "noun": noun, "is_new": instance is None, "delete_url": delete_url},
    )


@login_required
def climber_list(request: HttpRequest) -> HttpResponse:
    balances = selectors.season_balances()
    climbers = [
        (c, balances.get(c.pk, 0.0), c.todos.filter(sent_on__isnull=True).count())
        for c in Climber.objects.select_related("user")
    ]
    return render(request, "adventures/climbers.html", {"climbers": climbers})


@login_required
def climber_add(request: HttpRequest) -> HttpResponse:
    return _edit(request, ClimberForm, None, noun="climber", success_url="adventures:climbers")


@login_required
def climber_edit(request: HttpRequest, pk: int) -> HttpResponse:
    climber = get_object_or_404(Climber, pk=pk)
    return _edit(
        request,
        ClimberForm,
        climber,
        noun="climber",
        success_url=reverse("adventures:climber", args=[pk]),
        delete_url=reverse("adventures:delete", args=["climber", pk]),
    )


@login_required
def climber_detail(request: HttpRequest, pk: int) -> HttpResponse:
    climber = get_object_or_404(Climber, pk=pk)
    tick_form = TickImportForm()
    if request.method == "POST":
        tick_form = TickImportForm(request.POST, request.FILES)
        if tick_form.is_valid():
            try:
                rows = grades.parse_tick_export(
                    tick_form.cleaned_data["file"].read().decode("utf-8")
                )
            except (UnicodeDecodeError, ValueError) as exc:
                tick_form.add_error("file", str(exc))
            else:
                # The export is always the full history, so replace rather than merge.
                with transaction.atomic():
                    climber.ticks.all().delete()
                    Tick.objects.bulk_create(Tick(climber=climber, **row) for row in rows)
                messages.success(request, f"Imported {len(rows)} ticks for {climber}.")
                return redirect("adventures:climber", pk=pk)

    todos = list(climber.todos.select_related("crag__zone"))
    weights = ledger.todo_weights(todos)
    return render(
        request,
        "adventures/climber.html",
        {
            "climber": climber,
            "open_todos": [(t, weights[t.pk]) for t in todos if not t.is_sent],
            "sent_todos": sorted(
                (t for t in todos if t.is_sent), key=lambda t: t.sent_on, reverse=True
            ),
            "balance": selectors.season_balances().get(climber.pk, 0.0),
            "suggested": _suggested_level(climber),
            "window_days": grades.LEVEL_WINDOW_DAYS,
            "tick_form": tick_form,
            "recent_ticks": climber.ticks.all()[:10],
        },
    )


def _suggested_level(climber: Climber) -> grades.ClimbingLevel:
    return grades.climbing_level(climber.ticks.filter(grade__isnull=False), timezone.localdate())


@login_required
@require_POST
def climber_use_suggested(request: HttpRequest, pk: int) -> HttpResponse:
    """Copy the tick-based suggestions into the climber's grades (only where there is one)."""
    climber = get_object_or_404(Climber, pk=pk)
    suggested = _suggested_level(climber)
    if suggested.onsight_label:
        climber.onsight_grade = suggested.onsight_label
    if suggested.redpoint_label:
        climber.redpoint_grade = suggested.redpoint_label
    climber.save(update_fields=["onsight_grade", "redpoint_grade"])
    return redirect("adventures:climber", pk=pk)


@login_required
def todo_add(request: HttpRequest, climber_pk: int) -> HttpResponse:
    climber = get_object_or_404(Climber, pk=climber_pk)

    def append(todo: ToDo) -> None:
        todo.climber = climber
        todo.rank = (climber.todos.aggregate(Max("rank"))["rank__max"] or 0) + 1

    if request.method == "GET":
        zone_id = request.GET.get("zone")
        return render(
            request,
            "adventures/todo_form.html",
            {
                "form": ToDoForm(),
                "noun": f"to-do for {climber}",
                "is_new": True,
                "climber": climber,
                "zones": Zone.objects.exclude(openbeta_uuids=[]),
                "selected_zone": int(zone_id) if zone_id and zone_id.isdigit() else None,
            },
        )
    return _edit(
        request,
        ToDoForm,
        None,
        noun=f"to-do for {climber}",
        success_url=reverse("adventures:climber", args=[climber_pk]),
        on_create=append,
    )


def _climb_index(zone: Zone) -> list[dict]:
    """The zone's searchable climb list, refetched from OpenBeta when stale or relinked."""
    key = ",".join(str(uuid) for uuid in zone.openbeta_uuids)
    fresh = zone.climb_index_updated_at and (
        timezone.now() - zone.climb_index_updated_at < CLIMB_INDEX_TTL
    )
    if zone.climb_index_key != key or not fresh:
        zone.climb_index = [
            c for uuid in zone.openbeta_uuids for c in openbeta.region_climbs(uuid)
        ]
        zone.climb_index_key = key
        zone.climb_index_updated_at = timezone.now()
        zone.save(update_fields=["climb_index", "climb_index_key", "climb_index_updated_at"])
    return zone.climb_index


@login_required
def climb_search(request: HttpRequest, climber_pk: int) -> HttpResponse:
    climber = get_object_or_404(Climber, pk=climber_pk)
    zone = Zone.objects.filter(pk=request.GET.get("zone") or None).first()
    query = request.GET.get("q", "").strip()
    results, error = [], None
    if zone and len(query) >= 3:
        try:
            results = openbeta.search_climbs(_climb_index(zone), query)
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("OpenBeta climb index for %s failed: %s", zone, exc)
            error = "OpenBeta didn't respond - try again."
    on_list = set(climber.todos.exclude(openbeta_uuid="").values_list("openbeta_uuid", flat=True))
    return render(
        request,
        "adventures/partials/climb_results.html",
        {
            "climber": climber,
            "zone": zone,
            "results": results,
            "error": error,
            "query": query,
            "on_list": on_list,
        },
    )


def _crag_for(zone: Zone, crag_uuid: str, name: str) -> Crag:
    """The zone's crag for an OpenBeta sub-area: linked, same-named, or newly created."""
    crags = list(zone.crags.all())
    for crag in crags:
        if crag_uuid in crag.openbeta_uuids:
            return crag
    for crag in crags:
        if crag.name.casefold() == name.casefold():
            crag.openbeta_uuids = [*crag.openbeta_uuids, crag_uuid]
            crag.save(update_fields=["openbeta_uuids"])
            return crag
    return Crag.objects.create(
        zone=zone, name=name, sandstone=zone.sandstone, openbeta_uuids=[crag_uuid]
    )


@login_required
@require_POST
def todo_from_openbeta(request: HttpRequest, climber_pk: int) -> HttpResponse:
    climber = get_object_or_404(Climber, pk=climber_pk)
    zone = get_object_or_404(Zone, pk=request.POST.get("zone"))
    climb = next((c for c in zone.climb_index if c["uuid"] == request.POST.get("climb")), None)
    if climb is None:
        return HttpResponseBadRequest("Unknown climb - search again")
    if climber.todos.filter(openbeta_uuid=climb["uuid"], sent_on__isnull=True).exists():
        messages.info(request, f"{climb['name']} is already on {climber}'s list.")
        return redirect("adventures:climber", pk=climber.pk)
    crag = _crag_for(zone, climb["crag_uuid"], climb["crag"] or zone.name)
    ToDo.objects.create(
        climber=climber,
        crag=crag,
        name=climb["name"][:120],
        grade=climb["grade"][:20],
        openbeta_uuid=climb["uuid"],
        rank=(climber.todos.aggregate(Max("rank"))["rank__max"] or 0) + 1,
    )
    messages.success(
        request, f"Added {climb['name']} at {crag} - move it up the list if it's a priority."
    )
    return redirect("adventures:climber", pk=climber.pk)


@login_required
@require_POST
def zone_from_openbeta(request: HttpRequest) -> HttpResponse:
    """Use an OpenBeta area as a zone - an existing one if it's already linked."""
    uuid = request.POST.get("uuid", "")
    if not openbeta.is_uuid(uuid):
        return HttpResponseBadRequest("Not an OpenBeta area id")
    zone = next((z for z in Zone.objects.all() if uuid in z.openbeta_uuids), None)
    if zone is None:
        try:
            area = openbeta.area_info(uuid)
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("OpenBeta area %s failed: %s", uuid, exc)
            messages.error(request, "OpenBeta didn't respond - try again.")
            return redirect(request.POST.get("next") or "adventures:zones")
        name = area["name"]
        if Zone.objects.filter(name=name).exists():
            name = f"{name} ({request.POST.get('path') or 'OpenBeta'})"[:120]
        zone = Zone.objects.create(
            name=name,
            latitude=area["latitude"],
            longitude=area["longitude"],
            is_local=request.POST.get("is_local") == "on",
            openbeta_uuids=[uuid],
        )
        messages.success(request, f"Added zone {zone}. Edit it to set local/sandstone.")
    next_url = request.POST.get("next")
    if next_url and next_url.startswith("/"):
        return redirect(f"{next_url}?zone={zone.pk}")
    return redirect("adventures:zones")


@login_required
def todo_edit(request: HttpRequest, pk: int) -> HttpResponse:
    todo = get_object_or_404(ToDo, pk=pk)
    return _edit(
        request,
        ToDoForm,
        todo,
        noun="to-do",
        success_url=reverse("adventures:climber", args=[todo.climber_id]),
        delete_url=reverse("adventures:delete", args=["todo", pk]),
    )


@login_required
@require_POST
def todo_move(request: HttpRequest, pk: int, direction: str) -> HttpResponse:
    todo = get_object_or_404(ToDo, pk=pk, sent_on__isnull=True)
    open_todos = list(todo.climber.todos.filter(sent_on__isnull=True).order_by("rank", "pk"))
    i = open_todos.index(todo)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(open_todos):
        open_todos[i], open_todos[j] = open_todos[j], open_todos[i]
    for rank, item in enumerate(open_todos, start=1):
        item.rank = rank
    ToDo.objects.bulk_update(open_todos, ["rank"])
    return redirect("adventures:climber", pk=todo.climber_id)


@login_required
@require_POST
def todo_send(request: HttpRequest, pk: int) -> HttpResponse:
    todo = get_object_or_404(ToDo, pk=pk)
    todo.sent_on = None if todo.is_sent else timezone.localdate()
    if not todo.is_sent:
        # Back onto the list at the bottom.
        todo.rank = (todo.climber.todos.aggregate(Max("rank"))["rank__max"] or 0) + 1
    todo.save(update_fields=["sent_on", "rank"])
    _renumber(todo.climber_id)
    return redirect("adventures:climber", pk=todo.climber_id)


@login_required
def trip_list(request: HttpRequest) -> HttpResponse:
    today = timezone.localdate()
    trips = Trip.objects.select_related("zone")
    return render(
        request,
        "adventures/trips.html",
        {
            "upcoming": trips.filter(end_date__gte=today),
            "past": trips.filter(end_date__lt=today).order_by("-start_date"),
        },
    )


@login_required
def trip_add(request: HttpRequest) -> HttpResponse:
    return _edit(request, TripForm, None, noun="trip", success_url="adventures:trips")


@login_required
def trip_edit(request: HttpRequest, pk: int) -> HttpResponse:
    trip = get_object_or_404(Trip, pk=pk)
    return _edit(
        request,
        TripForm,
        trip,
        noun="trip",
        success_url="adventures:trips",
        delete_url=reverse("adventures:delete", args=["trip", pk]),
    )


@login_required
def zone_list(request: HttpRequest) -> HttpResponse:
    zones = Zone.objects.prefetch_related("crags")
    return render(request, "adventures/zones.html", {"zones": zones})


@login_required
def zone_add(request: HttpRequest) -> HttpResponse:
    return _edit(
        request,
        ZoneForm,
        None,
        noun="zone",
        success_url="adventures:zones",
        template="adventures/openbeta_form.html",
    )


@login_required
def zone_edit(request: HttpRequest, pk: int) -> HttpResponse:
    zone = get_object_or_404(Zone, pk=pk)
    return _edit(
        request,
        ZoneForm,
        zone,
        noun="zone",
        success_url="adventures:zones",
        delete_url=reverse("adventures:delete", args=["zone", pk]),
        template="adventures/openbeta_form.html",
    )


@login_required
def crag_add(request: HttpRequest) -> HttpResponse:
    zone = request.GET.get("zone")
    return _edit(
        request,
        CragForm,
        None,
        noun="crag",
        success_url="adventures:zones",
        initial={"zone": zone} if zone else None,
        template="adventures/openbeta_form.html",
    )


@login_required
def crag_edit(request: HttpRequest, pk: int) -> HttpResponse:
    crag = get_object_or_404(Crag, pk=pk)
    return _edit(
        request,
        CragForm,
        crag,
        noun="crag",
        success_url="adventures:zones",
        delete_url=reverse("adventures:delete", args=["crag", pk]),
        template="adventures/openbeta_form.html",
    )


@login_required
@require_POST
def refresh_route_counts(request: HttpRequest) -> HttpResponse:
    crags = [c for c in Crag.objects.select_related("zone") if c.openbeta_uuids]
    failed = []
    for crag in crags:
        try:
            crag.grade_counts = openbeta.fetch_grade_counts(crag.openbeta_uuids)
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("OpenBeta fetch for %s failed: %s", crag, exc)
            failed.append(str(crag))
            continue
        crag.grade_counts_updated_at = timezone.now()
        crag.save(update_fields=["grade_counts", "grade_counts_updated_at"])
    if failed:
        messages.error(request, f"Couldn't reach OpenBeta for: {', '.join(failed)}")
    else:
        messages.success(request, f"Route counts refreshed for {len(crags)} crags.")
    return redirect("adventures:zones")


@login_required
def openbeta_search(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()
    results, error = [], None
    if len(query) >= 3:
        try:
            results = openbeta.search_areas(query)
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("OpenBeta search failed: %s", exc)
            error = "OpenBeta didn't respond - try again."
    return render(
        request,
        "adventures/partials/openbeta_results.html",
        {
            "results": results,
            "error": error,
            "query": query,
            # "zone" mode offers each result as a new zone instead of an id to copy.
            "as_zone": request.GET.get("mode") == "zone",
            "next": request.GET.get("next", ""),
        },
    )


_DELETABLE = {
    "todo": ToDo,
    "climber": Climber,
    "trip": Trip,
    "zone": Zone,
    "crag": Crag,
}


@login_required
@require_POST
def delete(request: HttpRequest, kind: str, pk: int) -> HttpResponse:
    if kind not in _DELETABLE:
        return HttpResponseBadRequest("Unknown kind")
    obj = get_object_or_404(_DELETABLE[kind], pk=pk)
    success_url = {
        "todo": lambda: reverse("adventures:climber", args=[obj.climber_id]),
        "climber": lambda: reverse("adventures:climbers"),
        "trip": lambda: reverse("adventures:trips"),
        "zone": lambda: reverse("adventures:zones"),
        "crag": lambda: reverse("adventures:zones"),
    }[kind]()
    obj.delete()
    if isinstance(obj, ToDo):
        _renumber(obj.climber_id)
    return redirect(success_url)
