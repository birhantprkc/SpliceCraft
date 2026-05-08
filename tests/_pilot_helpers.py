"""Async helpers for Textual `pilot` tests.

Replaces the `await pilot.pause(0.3)` / `await pilot.pause(0.5)` pattern
with predicate-driven polling so tests don't paper over modal-mount or
worker-thread race conditions with fixed sleeps. The fixed-sleep
pattern is twice broken:
  * Too short on a loaded CI host → flaky failure (we already bumped
    one BLAST budget at release time).
  * Too long on a fast workstation → cumulative wasted seconds in
    the inner-loop pytest cycle.

These helpers poll a clear "ready" signal (modal mounted, widget
present, worker idle) and return as soon as the signal fires. Failure
case raises AssertionError with a descriptive message — never silently
times out.

Each helper accepts a `timeout` (default 2 s, generous enough for any
real handler on a slow host) and a `poll` interval (default 20 ms,
short enough that the helper returns quickly when the signal is
already true).
"""
from __future__ import annotations

import time
from typing import Any, Callable


async def wait_for_state(
    pilot,
    predicate: Callable[[], bool],
    *,
    timeout: float = 2.0,
    poll: float = 0.02,
    what: str = "condition",
) -> None:
    """Yield control via `pilot.pause(poll)` until `predicate()` returns
    True or `timeout` seconds elapse.

    Exceptions inside `predicate()` are swallowed — widgets may not
    exist yet, queries may raise NoMatches, etc. Polling continues
    until the predicate succeeds or the deadline hits.

    Raises AssertionError on timeout. Caller passes `what` to make
    the failure message useful (e.g. `"BlastModal mount"`).
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            if predicate():
                return
        except Exception:
            # Widgets may not exist yet; keep polling. This is the
            # whole point — the helper is more forgiving than
            # `assert pilot.app.screen.query_one(...)` at a fixed
            # delay would be.
            pass
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"wait_for_state timed out after {timeout}s waiting "
                f"for {what}"
            )
        await pilot.pause(poll)


async def wait_for_modal(
    pilot,
    screen_class_or_id,
    *,
    timeout: float = 2.0,
    poll: float = 0.02,
) -> None:
    """Wait until the topmost screen is an instance of `screen_class_or_id`
    (when a class is passed) OR has a matching `.id` (when a string is
    passed). Replaces `pilot.pause(0.3)` after `push_screen(...)`.

    Example:
        await app.push_screen(MutagenizeModal(...))
        await wait_for_modal(pilot, MutagenizeModal)
        # Now safe to query #mut-source.
    """
    if isinstance(screen_class_or_id, str):
        what = f"modal #{screen_class_or_id}"
        target_id = screen_class_or_id
        def _pred_modal_id() -> bool:
            return getattr(pilot.app.screen, "id", "") == target_id
        pred = _pred_modal_id
    else:
        what = f"modal {screen_class_or_id.__name__}"
        target_class = screen_class_or_id
        def _pred_modal_cls() -> bool:
            return isinstance(pilot.app.screen, target_class)
        pred = _pred_modal_cls
    await wait_for_state(
        pilot, pred, timeout=timeout, poll=poll, what=what,
    )


async def wait_for_no_workers(
    pilot,
    *,
    timeout: float = 5.0,
    poll: float = 0.02,
    group: "str | None" = None,
) -> None:
    """Wait until no Textual workers are running. With `group`, only
    workers in that group are considered.

    Replaces the `pilot.pause(0.5); pilot.pause(0.5)` pattern after
    triggering a `@work(thread=True)` callback. Polls `app.workers`
    so the wait returns the moment the worker hands its result back
    to the event loop, not after a fixed sleep.

    Default 5 s timeout: BLAST DB build on a synthetic 2.7 kb plasmid
    is 30-50 ms typical, but a real corpus + cold cache can stretch
    to ~2 s on CI; 5 s gives 2× headroom over that.
    """
    def _pred():
        try:
            workers = list(pilot.app.workers)
        except Exception:
            return True  # no worker mgr yet → "no workers"
        if group is not None:
            workers = [w for w in workers if getattr(w, "group", "") == group]
        # A worker is "done" when it's not running. State enum names
        # vary across Textual versions; check the boolean is_running
        # if available, else the state name.
        for w in workers:
            running = getattr(w, "is_running", None)
            if running is True:
                return False
            if running is None:
                state = getattr(w, "state", None)
                if state is not None and "RUNNING" in str(state).upper():
                    return False
        return True
    what = f"workers (group={group})" if group else "all workers idle"
    await wait_for_state(
        pilot, _pred, timeout=timeout, poll=poll, what=what,
    )


async def wait_for_widget(
    pilot,
    selector: str,
    *,
    timeout: float = 2.0,
    poll: float = 0.02,
) -> Any:
    """Wait until a widget matching the CSS selector exists in the
    current screen, then return it. Replaces `pilot.pause(0.3); w =
    app.screen.query_one(selector)` with a deterministic poll.
    """
    def _pred():
        try:
            return pilot.app.screen.query_one(selector) is not None
        except Exception:
            return False
    await wait_for_state(
        pilot, _pred, timeout=timeout, poll=poll,
        what=f"widget {selector!r}",
    )
    return pilot.app.screen.query_one(selector)
