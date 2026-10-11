from django.conf import settings
from django.db import models
from django.utils import timezone


class Medication(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="medications"
    )
    name = models.CharField(max_length=120)
    note = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    # When tracking actually starts for streak/completion purposes - editable,
    # since adding the medication to the app doesn't mean that's when you
    # started taking it. created_at is just the DB row's own timestamp.
    start_date = models.DateField(default=timezone.localdate)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class DoseLog(models.Model):
    medication = models.ForeignKey(Medication, on_delete=models.CASCADE, related_name="logs")
    date = models.DateField()
    taken = models.BooleanField(default=True)
    logged_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["medication", "date"], name="unique_dose_per_day")
        ]
        ordering = ["-date"]

    def __str__(self) -> str:
        return f"{self.medication} - {self.date}"
