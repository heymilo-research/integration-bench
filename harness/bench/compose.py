"""Shared Compose orchestration errors.

The only supported stack implementation is :class:`bench.compose_unit.ComposeUnitStack`.
"""


class ComposeError(RuntimeError):
    """A canonical Compose-unit lifecycle or rendering failure."""


class ParticipantResourceError(ComposeError):
    """A candidate-controlled process exceeded its declared resource policy."""


class ParticipantDiskLimitExceeded(ParticipantResourceError):
    """Participant-writable retained storage exceeded the task disk budget."""
