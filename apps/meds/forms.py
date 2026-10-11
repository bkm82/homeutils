from django import forms

from .models import Medication


class MedicationForm(forms.ModelForm):
    class Meta:
        model = Medication
        fields = ["name", "note", "start_date"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Name"}),
            "note": forms.TextInput(attrs={"placeholder": "Dose / notes (optional)"}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
        }
