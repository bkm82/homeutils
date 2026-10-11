from django import forms

from .models import NtfyConfig


class NtfyConfigForm(forms.ModelForm):
    class Meta:
        model = NtfyConfig
        fields = ["topic", "server_url", "reminder_time", "enabled"]
        widgets = {
            "topic": forms.TextInput(attrs={"placeholder": "ntfy topic"}),
            "server_url": forms.URLInput(attrs={"placeholder": "https://ntfy.sh"}),
            "reminder_time": forms.TimeInput(attrs={"type": "time"}),
        }
