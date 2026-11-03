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
from .forms import AreaForm, ClimberForm, TickImportForm, ToDoForm, TripForm
from .models import Area, Climber, Credit, Focus, Tick, ToDo, Trip, Weekend

logger = logging.getLogger(__name__)

# How far back the season page offers unlogged weekends for logging.
LOG_BACK_WEEKS = 8


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
    later_trips = Trip.objects.filter(start_date__gt=last_sunday).select_related("area")
    return render(
        request,
        "adventures/planner.html",
        {
            "weekends": weekends,
            "later_trips": later_trips,
            "focuses": Focus.choices,
            "today": today,
            "has_areas": Area.objects.exists(),
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


def _log_todos(attendee_ids: list[int], area_id: int | None) -> list[ToDo]:
    if not area_id:
        return []
    return list(
        ToDo.objects.filter(
            climber_id__in=attendee_ids, area_id=area_id, sent_on__isnull=True
        ).select_related("climber")
    )


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
        attendee_ids = [int(pk) for pk in request.POST.getlist("attendees") if pk.isdigit()]
        attendees = list(Climber.objects.filter(pk__in=attendee_ids))
        area = Area.objects.filter(pk=request.POST.get("area") or None).first()
        error = None
        if outcome not in (Weekend.Outcome.CLIMBED, Weekend.Outcome.RAINED_OUT):
            error = "Pick what happened."
        elif outcome == Weekend.Outcome.CLIMBED and (not area or not attendees):
            error = "Pick where you climbed and who came."
        if not error:
            _apply_log(day, outcome, area, attendees, request.POST)
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
            "areas": Area.objects.all(),
            "credits": weekend.credits.select_related("climber") if weekend else [],
        },
    )


@transaction.atomic
def _apply_log(day: date, outcome: str, area: Area | None, attendees: list[Climber], post):
    weekend = _weekend(day)
    weekend.attendees.set(attendees)
    weekend.outcome = outcome
    weekend.area = area if outcome == Weekend.Outcome.CLIMBED else None
    weekend.save(update_fields=["outcome", "area"])
    weekend.credits.all().delete()
    if outcome != Weekend.Outcome.CLIMBED or area is None:
        return

    # Credit is worked out from the lists as they stood going in, before
    # this weekend's sends come off them.
    values = ledger.crag_values(ToDo.objects.filter(sent_on__isnull=True))
    Credit.objects.bulk_create(
        Credit(weekend=weekend, climber_id=cid, value=value, share=share)
        for cid, value, share in ledger.split_credit([c.pk for c in attendees], area.pk, values)
    )
    eligible = {t.pk: t for t in _log_todos([c.pk for c in attendees], area.pk)}
    worked = {int(pk) for pk in post.getlist("worked") if pk.isdigit()}
    sent = {int(pk) for pk in post.getlist("sent") if pk.isdigit()}
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
    attendee_ids = [int(pk) for pk in request.GET.getlist("attendees") if pk.isdigit()]
    area_id = request.GET.get("area")
    todos = _log_todos(attendee_ids, int(area_id) if area_id and area_id.isdigit() else None)
    return render(request, "adventures/partials/log_todos.html", {"todos": todos})


@login_required
@require_POST
def undo_log(request: HttpRequest, saturday: str) -> HttpResponse:
    weekend = get_object_or_404(Weekend, saturday=_saturday(saturday))
    with transaction.atomic():
        weekend.credits.all().delete()
        weekend.outcome = Weekend.Outcome.PLANNED
        weekend.area = None
        weekend.save(update_fields=["outcome", "area"])
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
    logged = Weekend.objects.exclude(outcome="").select_related("area")
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

    todos = list(climber.todos.select_related("area"))
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

    return _edit(
        request,
        ToDoForm,
        None,
        noun=f"to-do for {climber}",
        success_url=reverse("adventures:climber", args=[climber_pk]),
        on_create=append,
    )


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
    trips = Trip.objects.select_related("area")
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
def area_list(request: HttpRequest) -> HttpResponse:
    return render(request, "adventures/areas.html", {"areas": Area.objects.all()})


@login_required
def area_add(request: HttpRequest) -> HttpResponse:
    return _edit(
        request,
        AreaForm,
        None,
        noun="area",
        success_url="adventures:areas",
        template="adventures/area_form.html",
    )


@login_required
def area_edit(request: HttpRequest, pk: int) -> HttpResponse:
    area = get_object_or_404(Area, pk=pk)
    return _edit(
        request,
        AreaForm,
        area,
        noun="area",
        success_url="adventures:areas",
        delete_url=reverse("adventures:delete", args=["area", pk]),
        template="adventures/area_form.html",
    )


@login_required
@require_POST
def refresh_route_counts(request: HttpRequest) -> HttpResponse:
    areas = [area for area in Area.objects.all() if area.openbeta_uuids]
    failed = []
    for area in areas:
        try:
            area.grade_counts = openbeta.fetch_grade_counts(area.openbeta_uuids)
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("OpenBeta fetch for %s failed: %s", area, exc)
            failed.append(area.name)
            continue
        area.grade_counts_updated_at = timezone.now()
        area.save(update_fields=["grade_counts", "grade_counts_updated_at"])
    if failed:
        messages.error(request, f"Couldn't reach OpenBeta for: {', '.join(failed)}")
    else:
        messages.success(request, f"Route counts refreshed for {len(areas)} areas.")
    return redirect("adventures:areas")


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
        {"results": results, "error": error, "query": query},
    )


_DELETABLE = {
    "todo": ToDo,
    "climber": Climber,
    "trip": Trip,
    "area": Area,
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
        "area": lambda: reverse("adventures:areas"),
    }[kind]()
    obj.delete()
    if isinstance(obj, ToDo):
        _renumber(obj.climber_id)
    return redirect(success_url)
