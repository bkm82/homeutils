from django.db import migrations

# Starter areas - edit or delete them in the app. Coordinates are approximate;
# they only need to be close enough for a regional forecast. The OpenBeta ids
# link route counts (Areas -> "Refresh route counts" loads them).
FLAGSTAFF_AREA = "1fdb38a6-6b41-5d20-993c-dcf46fcf25a3"
PARADISE_FORKS = "0fd88af2-a1c9-51d8-b0ef-e95502af2bdc"
SEDONA_AREA = "19dfb197-abbe-5b60-abc0-83f875a4e73b"
RED_ROCKS = "bea6bf11-de53-5046-a5b4-b89217b7e9bc"
INDIAN_CREEK = "f166e672-4a52-56d3-94f1-14c876feb670"
POTASH_ROAD = "27051d4b-092e-56bb-9bb4-9aea5cdf319c"

AREAS = [
    (
        "Flagstaff",
        35.1983,
        -111.6513,
        ["climbing", "hiking"],
        False,
        "Local basalt/limestone",
        [FLAGSTAFF_AREA, PARADISE_FORKS],
    ),
    (
        "Sedona",
        34.8697,
        -111.7610,
        ["climbing", "hiking"],
        True,
        "Better as temps drop",
        [SEDONA_AREA],
    ),
    ("Red Rock (Las Vegas)", 36.1355, -115.4270, ["climbing", "hiking"], True, "", [RED_ROCKS]),
    ("Indian Creek", 38.0260, -109.5400, ["climbing"], True, "", [INDIAN_CREEK]),
    ("Moab", 38.5733, -109.5498, ["climbing", "hiking"], True, "Potash Road", [POTASH_ROAD]),
    ("Arizona Snowbowl", 35.3306, -111.7106, ["skiing"], False, "", []),
    ("Ouray Ice Park", 38.0228, -107.6714, ["ice"], False, "", []),
]


def seed(apps, schema_editor):
    Area = apps.get_model("adventures", "Area")
    for name, lat, lon, activities, sandstone, notes, uuids in AREAS:
        Area.objects.get_or_create(
            name=name,
            defaults={
                "latitude": lat,
                "longitude": lon,
                "activities": activities,
                "sandstone": sandstone,
                "notes": notes,
                "openbeta_uuids": uuids,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("adventures", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
