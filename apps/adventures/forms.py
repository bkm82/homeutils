from django import forms
from django.contrib.auth import get_user_model

from . import grades, openbeta
from .models import Climber, Crag, ToDo, Trip, Zone


class PersonChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj) -> str:
        return obj.get_full_name() or obj.get_username()


class UUIDListField(forms.CharField):
    """Comma/whitespace-separated OpenBeta area uuids <-> list."""

    def prepare_value(self, value):
        return ", ".join(value) if isinstance(value, list) else value

    def to_python(self, value) -> list[str]:
        uuids = [u for u in (value or "").replace(",", " ").split() if u]
        invalid = [u for u in uuids if not openbeta.is_uuid(u)]
        if invalid:
            raise forms.ValidationError(f"Not an OpenBeta area id: {', '.join(invalid)}")
        return list(dict.fromkeys(uuids))


class ZoneForm(forms.ModelForm):
    class Meta:
        model = Zone
        fields = ["name", "latitude", "longitude", "is_local", "notes"]
        widgets = {
            "latitude": forms.NumberInput(attrs={"step": "any"}),
            "longitude": forms.NumberInput(attrs={"step": "any"}),
        }
        help_texts = {"latitude": "The zone's forecast point - its crags share it."}


class CragForm(forms.ModelForm):
    openbeta_uuids = UUIDListField(
        required=False,
        label="OpenBeta areas",
        help_text="Search below to add. Don't list an area together with one inside it "
        "- sub-areas are already counted.",
        widget=forms.TextInput(attrs={"placeholder": "OpenBeta area ids"}),
    )

    class Meta:
        model = Crag
        fields = ["zone", "name", "sandstone", "notes", "openbeta_uuids"]


class ClimberForm(forms.ModelForm):
    user = PersonChoiceField(
        queryset=get_user_model().objects.none(),
        required=False,
        label="Login (optional)",
        help_text="Link a login so this climber's profile is theirs by default.",
    )

    class Meta:
        model = Climber
        fields = ["name", "user", "onsight_grade", "redpoint_grade", "is_active"]
        widgets = {
            "onsight_grade": forms.TextInput(attrs={"placeholder": "e.g. 5.10c"}),
            "redpoint_grade": forms.TextInput(attrs={"placeholder": "e.g. 5.11d"}),
        }
        help_texts = {
            "onsight_grade": "Hardest grade they reliably onsight. Volume weekends aim at routes "
            "up to three letter grades below it.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = get_user_model().objects.filter(is_active=True)
        self.fields["is_active"].label = "In the group this season"

    def _clean_grade(self, field: str) -> str:
        value = self.cleaned_data[field].strip()
        if value and grades.parse_yds(value) is None:
            raise forms.ValidationError("Use a YDS grade like 5.10c or 5.11+.")
        return value

    def clean_onsight_grade(self) -> str:
        return self._clean_grade("onsight_grade")

    def clean_redpoint_grade(self) -> str:
        return self._clean_grade("redpoint_grade")


class ToDoForm(forms.ModelForm):
    class Meta:
        model = ToDo
        fields = ["name", "crag", "grade", "is_project", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Pine Tree Arete"}),
            "grade": forms.TextInput(attrs={"placeholder": "e.g. 5.11a"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["crag"].queryset = Crag.objects.select_related("zone")


class TripForm(forms.ModelForm):
    class Meta:
        model = Trip
        fields = ["title", "zone", "start_date", "end_date", "with_whom", "notes"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "e.g. Moab with the crew"}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "with_whom": forms.TextInput(attrs={"placeholder": "Who's coming (optional)"}),
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "End date can't be before the start date.")
        return cleaned


class TickImportForm(forms.Form):
    file = forms.FileField(label="Mountain Project tick export (CSV)")
