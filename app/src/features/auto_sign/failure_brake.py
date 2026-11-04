"""Stops the sign-and-roll-back loop on one document.

Memory only, so each gunicorn worker counts for itself and a restart forgets: with 4 workers a document
can fail up to 12 times before every worker has paused it.
"""

import time
from collections.abc import Callable

FAILURES_BEFORE_PAUSE = 3
PAUSE_SECONDS = 3600.0


class SigningFailureBrake:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._failures: dict[object, int] = {}
        self._paused_until: dict[object, float] = {}

    def is_paused(self, sign_id: object) -> bool:
        paused_until = self._paused_until.get(sign_id)
        if paused_until is None:
            return False
        if self._clock() < paused_until:
            return True
        self.note_success(sign_id)
        return False

    def note_failure(self, sign_id: object) -> bool:
        """True when this failure pauses the document."""
        failures = self._failures.get(sign_id, 0) + 1
        self._failures[sign_id] = failures
        if failures < FAILURES_BEFORE_PAUSE:
            return False
        self._paused_until[sign_id] = self._clock() + PAUSE_SECONDS
        return True

    def note_success(self, sign_id: object) -> None:
        self._failures.pop(sign_id, None)
        self._paused_until.pop(sign_id, None)


signing_failure_brake = SigningFailureBrake()
