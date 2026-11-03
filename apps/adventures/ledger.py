"""Season-long fairness between climbers.

- Each climber's open to-dos are weighted by their place in that climber's
  list: #1 is worth 1, #2 is worth 1/2, #3 is worth 1/3, ...
- What a crag is worth to someone is the sum of their to-do weights there.
- The group climbs one crag per day. When a weekend is logged, each climbed
  day credits each attendee with half that crag's worth to them, and entitles
  them to half an equal share of the day's total.
  `balance = sum(share - value)` over the season: positive means owed.
- Rained-out weekends credit nobody, so whoever was owed stays owed.
- The planner multiplies each attendee's crag value by their need weight
  (1 + balance), so the people most owed pull the recommendation their way.
"""

from collections import defaultdict
from collections.abc import Iterable

# Someone well ahead still counts a little - otherwise a crag only they care
# about would look worthless to the group.
MIN_NEED_WEIGHT = 0.2


def todo_weights(todos: Iterable) -> dict[int, float]:
    """Weight per open to-do id, from its position in its climber's list."""
    by_climber = defaultdict(list)
    for todo in todos:
        if todo.sent_on is None:
            by_climber[todo.climber_id].append(todo)
    weights = {}
    for climber_todos in by_climber.values():
        climber_todos.sort(key=lambda t: (t.rank, t.pk))
        for position, todo in enumerate(climber_todos, start=1):
            weights[todo.pk] = 1 / position
    return weights


def crag_values(todos: Iterable, projects_only: bool = False) -> dict[tuple[int, int], float]:
    """What each crag is worth to each climber: {(climber_id, crag_id): value}."""
    todos = list(todos)
    weights = todo_weights(todos)
    values: dict[tuple[int, int], float] = defaultdict(float)
    for todo in todos:
        if todo.pk in weights and (todo.is_project or not projects_only):
            values[(todo.climber_id, todo.crag_id)] += weights[todo.pk]
    return dict(values)


def balances(credits: Iterable) -> dict[int, float]:
    """How much each climber is owed (positive) or ahead (negative), by climber id."""
    totals: dict[int, float] = defaultdict(float)
    for credit in credits:
        totals[credit.climber_id] += credit.share - credit.value
    return dict(totals)


def need_weight(balance: float) -> float:
    return max(MIN_NEED_WEIGHT, 1 + balance)


def split_credit(
    attendee_ids: Iterable[int], crag_ids: Iterable[int], values: dict[tuple[int, int], float]
) -> dict[int, tuple[float, float]]:
    """{climber_id: (value, share)} for a weekend climbed at `crag_ids`, one per climbed day.

    Each day is half a weekend: a two-day weekend at one crag credits the same
    as the old whole-weekend credit, and a one-day weekend credits half.
    """
    attendee_ids = list(attendee_ids)
    credits = {cid: (0.0, 0.0) for cid in attendee_ids}
    if not attendee_ids:
        return credits
    for crag_id in crag_ids:
        received = {cid: values.get((cid, crag_id), 0.0) for cid in attendee_ids}
        share = sum(received.values()) / len(attendee_ids)
        for cid in attendee_ids:
            value, total_share = credits[cid]
            credits[cid] = (value + received[cid] / 2, total_share + share / 2)
    return credits
