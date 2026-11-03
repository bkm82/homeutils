"""Season-long fairness between climbers.

- Each climber's open to-dos are weighted by their place in that climber's
  list: #1 is worth 1, #2 is worth 1/2, #3 is worth 1/3, ...
- What a crag is worth to someone is the sum of their to-do weights there.
- When a climbed weekend is logged, each attendee is credited with the crag's
  worth to them, and is entitled to an equal share of the weekend's total.
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
    """What each crag is worth to each climber: {(climber_id, area_id): value}."""
    todos = list(todos)
    weights = todo_weights(todos)
    values: dict[tuple[int, int], float] = defaultdict(float)
    for todo in todos:
        if todo.pk in weights and (todo.is_project or not projects_only):
            values[(todo.climber_id, todo.area_id)] += weights[todo.pk]
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
    attendee_ids: Iterable[int], area_id: int, values: dict[tuple[int, int], float]
) -> list[tuple[int, float, float]]:
    """(climber_id, value, share) for each attendee of a weekend climbed at `area_id`."""
    attendee_ids = list(attendee_ids)
    if not attendee_ids:
        return []
    received = {cid: values.get((cid, area_id), 0.0) for cid in attendee_ids}
    share = sum(received.values()) / len(attendee_ids)
    return [(cid, received[cid], share) for cid in attendee_ids]
