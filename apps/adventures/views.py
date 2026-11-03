import logging
from datetime import date

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Model
from django.forms import ModelForm
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import grades, openbeta, selectors
from .forms import AreaForm, GoalForm, TickImportForm, TripForm
from .models import Area, Focus, Goal, Tick, Trip, Weekend

logger = logging.getLogger(__name__)


def _people():
    return get_user_model().objects.filter(is_active=True).order_by("username")


@login_required
def planner(request: HttpRequest) -> HttpResponse:
    today = timezone.localdate()
    saturdays = selectors.weekend_saturdays(today)
    weekends = selectors.build_weekends(saturdays, today, request.user)
    last_sunday = weekends[-1].sunday if weekends else today
    later_trips = Trip.objects.filter(start_date__gt=last_sunday).select_related("area")
    return render(
        request,
        "adventures/planner.html",
        {
            "weekends": weekends,
            "later_trips": later_trips,
            "people": _people(),
            "focuses": Focus.choices,
            "has_areas": Area.objects.exists(),
            "updated_at": selectors.forecast_updated_at(),
        },
    )


@login_required
@require_POST
def update_weekend(request: HttpRequest, saturday: str) -> HttpResponse:
    """Set whose weekend it is and/or its training focus - whichever was posted."""
    try:
        day = date.fromisoformat(saturday)
    except ValueError:
        return HttpResponseBadRequest("Invalid date")
    if day.weekday() != 5:
        return HttpResponseBadRequest("Not a Saturday")

    weekend, _ = Weekend.objects.get_or_create(saturday=day)
    if "person" in request.POST:
        person_id = request.POST["person"]
        weekend.priority = get_object_or_404(_people(), pk=person_id) if person_id else None
    if "focus" in request.POST:
        if request.POST["focus"] not in Focus.values:
            return HttpResponseBadRequest("Unknown focus")
        weekend.focus = request.POST["focus"]
    weekend.save()

    if request.htmx:
        [outlook] = selectors.build_weekends([day], timezone.localdate(), request.user)
        return render(
            request,
            "adventures/partials/weekend.html",
            {"weekend": outlook, "people": _people(), "focuses": Focus.choices},
        )
    return redirect("adventures:planner")


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
) -> HttpResponse:
    if request.method == "POST":
        form = form_class(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            return redirect(success_url)
    else:
        form = form_class(instance=instance, initial=initial)
    return render(
        request,
        template,
        {"form": form, "noun": noun, "is_new": instance is None, "delete_url": delete_url},
    )


@login_required
def goal_list(request: HttpRequest) -> HttpResponse:
    goals = Goal.objects.select_related("owner", "area")
    return render(request, "adventures/goals.html", {"goals": goals})


@login_required
def goal_add(request: HttpRequest) -> HttpResponse:
    return _edit(
        request,
        GoalForm,
        None,
        noun="goal",
        success_url="adventures:goals",
        initial={"owner": request.user},
    )


@login_required
def goal_edit(request: HttpRequest, pk: int) -> HttpResponse:
    goal = get_object_or_404(Goal, pk=pk)
    return _edit(
        request,
        GoalForm,
        goal,
        noun="goal",
        success_url="adventures:goals",
        delete_url=reverse("adventures:delete", args=["goal", pk]),
    )


@login_required
@require_POST
def goal_toggle(request: HttpRequest, pk: int) -> HttpResponse:
    goal = get_object_or_404(Goal, pk=pk)
    goal.is_done = not goal.is_done
    goal.save(update_fields=["is_done"])
    return redirect("adventures:goals")


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


@login_required
def ticks(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = TickImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                rows = grades.parse_tick_export(form.cleaned_data["file"].read().decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                form.add_error("file", str(exc))
            else:
                # The export is always your full history, so replace rather than merge.
                Tick.objects.filter(owner=request.user).delete()
                Tick.objects.bulk_create(Tick(owner=request.user, **row) for row in rows)
                messages.success(request, f"Imported {len(rows)} ticks.")
                return redirect("adventures:ticks")
    else:
        form = TickImportForm()

    today = timezone.localdate()
    levels = [
        (person, grades.climbing_level(person.ticks.filter(grade__isnull=False), today))
        for person in _people()
    ]
    return render(
        request,
        "adventures/ticks.html",
        {
            "form": form,
            "levels": levels,
            "recent": Tick.objects.filter(owner=request.user)[:25],
            "window_days": grades.LEVEL_WINDOW_DAYS,
        },
    )


_DELETABLE = {
    "goal": (Goal, "adventures:goals"),
    "trip": (Trip, "adventures:trips"),
    "area": (Area, "adventures:areas"),
}


@login_required
@require_POST
def delete(request: HttpRequest, kind: str, pk: int) -> HttpResponse:
    if kind not in _DELETABLE:
        return HttpResponseBadRequest("Unknown kind")
    model, success_url = _DELETABLE[kind]
    get_object_or_404(model, pk=pk).delete()
    return redirect(success_url)
