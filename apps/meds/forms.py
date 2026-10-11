from django import forms

from .models import Medication


class MedicationForm(forms.ModelForm):
    class Meta:
        model = Medication
        fields = ["name", "note"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Name"}),
            "note": forms.TextInput(attrs={"placeholder": "Dose / notes (optional)"}),
        }
