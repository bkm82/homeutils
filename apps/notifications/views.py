from urllib.error import URLError

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import NtfyConfigForm, ReminderTimeForm
from .models import NtfyConfig, ReminderTime
from .ntfy import send_ntfy


@login_required
def settings_view(request: HttpRequest) -> HttpResponse:
    config, _ = NtfyConfig.objects.get_or_create(user=request.user)
    if request.method == "POST":
        form = NtfyConfigForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            messages.success(request, "Saved.")
            return redirect("notifications:settings")
    else:
        form = NtfyConfigForm(instance=config)
    return render(
        request,
        "notifications/settings.html",
        {
            "form": form,
            "time_form": ReminderTimeForm(),
            "reminder_times": config.reminder_times.all(),
        },
    )


@login_required
@require_POST
def add_reminder_time(request: HttpRequest) -> HttpResponse:
    config, _ = NtfyConfig.objects.get_or_create(user=request.user)
    form = ReminderTimeForm(request.POST)
    if form.is_valid():
        reminder = form.save(commit=False)
        reminder.config = config
        try:
            with transaction.atomic():
                reminder.save()
        except IntegrityError:
            messages.error(request, "That time is already in your list.")
        else:
            messages.success(request, "Saved.")
    return redirect("notifications:settings")


@login_required
@require_POST
def remove_reminder_time(request: HttpRequest, pk: int) -> HttpResponse:
    reminder = get_object_or_404(ReminderTime, pk=pk, config__user=request.user)
    reminder.delete()
    return redirect("notifications:settings")


@login_required
@require_POST
def send_test(request: HttpRequest) -> HttpResponse:
    config, _ = NtfyConfig.objects.get_or_create(user=request.user)
    if not config.topic:
        messages.error(request, "Set a topic and save before sending a test.")
        return redirect("notifications:settings")

    try:
        send_ntfy(config.server_url, config.topic, "Test notification", "It works.")
    except URLError as exc:
        messages.error(request, f"Could not reach {config.server_url}: {exc}")
        return redirect("notifications:settings")

    messages.success(request, "Test notification sent.")
    return redirect("notifications:settings")
