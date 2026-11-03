from django import forms
from django.contrib.auth import get_user_model

from . import openbeta
from .models import Activity, Area, Goal, Trip


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


class AreaForm(forms.ModelForm):
    activities = forms.MultipleChoiceField(
        choices=Activity.choices, widget=forms.CheckboxSelectMultiple
    )
    openbeta_uuids = UUIDListField(
        required=False,
        label="OpenBeta areas",
        help_text="Search below to add. Don't list an area together with one inside it "
        "- sub-areas are already counted.",
        widget=forms.TextInput(attrs={"placeholder": "OpenBeta area ids"}),
    )

    class Meta:
        model = Area
        fields = [
            "name",
            "latitude",
            "longitude",
            "activities",
            "sandstone",
            "notes",
            "openbeta_uuids",
        ]
        widgets = {
            "latitude": forms.NumberInput(attrs={"step": "any"}),
            "longitude": forms.NumberInput(attrs={"step": "any"}),
        }


class GoalForm(forms.ModelForm):
    owner = PersonChoiceField(queryset=get_user_model().objects.none(), label="Whose goal")

    class Meta:
        model = Goal
        fields = ["name", "area", "activity", "grade", "owner", "notes"]
        widgets = {
            "name": forms.TextInput(
                attrs={"placeholder": "e.g. Paradise Forks - Pine Tree Arete"}
            ),
            "grade": forms.TextInput(attrs={"placeholder": "e.g. 5.11a"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = get_user_model().objects.filter(is_active=True)


class TripForm(forms.ModelForm):
    class Meta:
        model = Trip
        fields = ["title", "area", "activity", "start_date", "end_date", "with_whom", "notes"]
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
