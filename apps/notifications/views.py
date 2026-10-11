from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .forms import NtfyConfigForm
from .models import NtfyConfig
from .ntfy import send_ntfy


@login_required
def settings_view(request: HttpRequest) -> HttpResponse:
    config, _ = NtfyConfig.objects.get_or_create(user=request.user)
    if request.method == "POST":
        form = NtfyConfigForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            return redirect("notifications:settings")
    else:
        form = NtfyConfigForm(instance=config)
    return render(request, "notifications/settings.html", {"form": form})


@login_required
@require_POST
def send_test(request: HttpRequest) -> HttpResponse:
    config, _ = NtfyConfig.objects.get_or_create(user=request.user)
    send_ntfy(config.server_url, config.topic, "Test notification", "It works.")
    messages.success(request, "Test notification sent.")
    return redirect("notifications:settings")
