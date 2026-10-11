from django.contrib import admin

from .models import DoseLog, Medication


@admin.register(Medication)
class MedicationAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "is_active", "created_at"]
    list_filter = ["is_active"]


@admin.register(DoseLog)
class DoseLogAdmin(admin.ModelAdmin):
    list_display = ["medication", "date", "taken"]
    list_filter = ["taken"]
