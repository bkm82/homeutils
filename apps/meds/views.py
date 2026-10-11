from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import selectors
from .forms import MedicationForm
from .models import DoseLog, Medication


def _card_context(medication: Medication) -> dict:
    today = timezone.localdate()
    return {
        "medication": medication,
        "today": today,
        "streak": selectors.current_streak(medication, today),
        "completion": selectors.completion_rate(medication, today=today),
        "heatmap": selectors.heatmap(medication, today=today),
        "taken_today": medication.logs.filter(date=today, taken=True).exists(),
    }


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    medications = Medication.objects.filter(owner=request.user, is_active=True)
    cards = [_card_context(medication) for medication in medications]
    return render(request, "meds/dashboard.html", {"cards": cards})


@login_required
def add_medication(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = MedicationForm(request.POST)
        if form.is_valid():
            medication = form.save(commit=False)
            medication.owner = request.user
            medication.save()
            return redirect("meds:dashboard")
    else:
        form = MedicationForm()
    return render(request, "meds/medication_form.html", {"form": form})


@login_required
@require_POST
def archive_medication(request: HttpRequest, pk: int) -> HttpResponse:
    medication = get_object_or_404(Medication, pk=pk, owner=request.user)
    medication.is_active = False
    medication.save(update_fields=["is_active"])
    return redirect("meds:dashboard")


@login_required
@require_POST
def toggle_dose(request: HttpRequest, pk: int) -> HttpResponse:
    medication = get_object_or_404(Medication, pk=pk, owner=request.user)
    today = timezone.localdate()

    date_str = request.POST.get("date")
    if date_str:
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return HttpResponseBadRequest("Invalid date")
        if day > today:
            return HttpResponseBadRequest("Cannot log a future date")
    else:
        day = today

    log, created = DoseLog.objects.get_or_create(medication=medication, date=day)
    if not created:
        log.taken = not log.taken
        log.save(update_fields=["taken"])

    if request.htmx:
        return render(request, "meds/partials/medication_card.html", _card_context(medication))
    return redirect("meds:dashboard")
