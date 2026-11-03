"""OpenBeta (https://openbeta.io) - open climbing route data, used for route counts by grade.

The API can be slow, so it's only called on explicit user action (search,
refresh), never while rendering the planner.
"""

import json
import re
import urllib.request
from collections import Counter

OPENBETA_URL = "https://api.openbeta.io/"
TIMEOUT = 60
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def is_uuid(value: str) -> bool:
    return bool(_UUID.match(value))


def _query(query: str, variables: dict | None = None) -> dict:
    request = urllib.request.Request(
        OPENBETA_URL,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        # The API rejects urllib's default User-Agent with a 403.
        headers={"Content-Type": "application/json", "User-Agent": "homeutils/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        payload = json.load(response)
    if payload.get("errors"):
        raise ValueError(payload["errors"][0].get("message", "OpenBeta error"))
    return payload["data"]


def search_areas(name: str, limit: int = 15) -> list[dict]:
    """Areas matching `name`, biggest first: [{uuid, name, path, total}]."""
    data = _query(
        "query($name: String!, $limit: Int) {"
        " areas(filter: {area_name: {match: $name}}, limit: $limit)"
        " { uuid area_name pathTokens totalClimbs } }",
        {"name": name, "limit": limit},
    )
    results = [
        {
            "uuid": area["uuid"],
            "name": area["area_name"],
            # Drop the country and the area's own name - the rest locates it.
            "path": " › ".join(area["pathTokens"][1:-1]),
            "total": area["totalClimbs"],
        }
        for area in data["areas"]
    ]
    return sorted(results, key=lambda r: r["total"], reverse=True)


def fetch_grade_counts(uuids: list[str]) -> dict[str, int]:
    """Climb counts by grade label, summed over every area (and its sub-areas) in `uuids`."""
    # Only validated uuids are ever interpolated into the query.
    uuids = [uuid for uuid in uuids if is_uuid(uuid)]
    if not uuids:
        return {}
    fields = " ".join(
        f'a{i}: area(uuid: "{uuid}") {{ aggregate {{ byGrade {{ label count }} }} }}'
        for i, uuid in enumerate(uuids)
    )
    data = _query(f"{{ {fields} }}")
    counts: Counter[str] = Counter()
    for area in data.values():
        if area is None:  # unknown uuid
            continue
        for bucket in area["aggregate"]["byGrade"]:
            counts[bucket["label"]] += bucket["count"]
    return dict(counts)
