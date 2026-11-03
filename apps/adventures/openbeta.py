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


def area_info(uuid: str) -> dict:
    """{uuid, name, latitude, longitude} for one OpenBeta area."""
    data = _query(
        "query($uuid: ID) { area(uuid: $uuid) { uuid area_name metadata { lat lng } } }",
        {"uuid": uuid},
    )
    area = data["area"]
    if area is None:
        raise ValueError(f"No OpenBeta area {uuid}")
    return {
        "uuid": area["uuid"],
        "name": area["area_name"],
        "latitude": area["metadata"]["lat"],
        "longitude": area["metadata"]["lng"],
    }


BULK_PAGE = 500


def region_climbs(region_uuid: str) -> list[dict]:
    """Every climb under an OpenBeta area, each tagged with the crag it belongs to.

    The crag is the region's direct sub-area on the climb's path (e.g. Red Rocks ›
    Juniper Canyon for Crimson Chrysalis), or the region itself for climbs
    attached straight to it.
    """
    region = _query("query($uuid: ID) { area(uuid: $uuid) { ancestors } }", {"uuid": region_uuid})
    if region["area"] is None:
        raise ValueError(f"No OpenBeta area {region_uuid}")
    # `ancestors` ends with the area itself, so a descendant's ancestors[depth]
    # is the region's direct child on its path.
    ancestors = region["area"]["ancestors"]
    depth = len(ancestors)
    areas = []
    while True:
        page = _query(
            "query($ancestors: [String!]!, $limit: Int, $offset: Int) {"
            " bulkAreas(ancestors: $ancestors, limit: $limit, offset: $offset)"
            " { uuid area_name ancestors climbs { uuid name grades { yds vscale } } } }",
            {"ancestors": ancestors, "limit": BULK_PAGE, "offset": len(areas)},
        )["bulkAreas"]
        areas.extend(page)
        if len(page) < BULK_PAGE:
            break
    names = {area["uuid"]: area["area_name"] for area in areas}
    climbs = []
    for area in areas:
        crag_uuid = area["ancestors"][depth] if len(area["ancestors"]) > depth else region_uuid
        path = [names.get(uuid, "") for uuid in area["ancestors"][depth:]]
        for climb in area["climbs"]:
            grades = climb.get("grades") or {}
            climbs.append(
                {
                    "uuid": climb["uuid"],
                    "name": climb["name"],
                    "grade": grades.get("yds") or grades.get("vscale") or "",
                    "crag_uuid": crag_uuid,
                    "crag": names.get(crag_uuid, ""),
                    "path": " › ".join(p for p in path if p),
                }
            )
    return climbs


def search_climbs(index: list[dict], query: str, limit: int = 20) -> list[dict]:
    """Climbs whose name contains `query` (case-insensitive), prefix matches first."""
    query = query.casefold().strip()
    if not query:
        return []
    matches = [c for c in index if query in c["name"].casefold()]
    matches.sort(key=lambda c: (not c["name"].casefold().startswith(query), c["name"]))
    return matches[:limit]
