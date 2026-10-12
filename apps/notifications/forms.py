from django import forms

from .models import NtfyConfig, ReminderTime


class NtfyConfigForm(forms.ModelForm):
    class Meta:
        model = NtfyConfig
        fields = ["topic", "server_url", "enabled"]
        widgets = {
            "topic": forms.TextInput(attrs={"placeholder": "ntfy topic"}),
            "server_url": forms.URLInput(attrs={"placeholder": "https://ntfy.sh"}),
        }


class ReminderTimeForm(forms.ModelForm):
    class Meta:
        model = ReminderTime
        fields = ["time"]
        widgets = {
            "time": forms.TimeInput(attrs={"type": "time"}),
        }
