"""Failures an assistant can read and act on."""

from aicad.labels import strip_hidden


class AICADError(Exception):
    """An expected failure. The message is meant for a person or an assistant."""

    def __str__(self) -> str:
        return strip_hidden(super().__str__())


class ScriptError(AICADError):
    """A part script did not produce a usable solid."""


class GeometryError(AICADError):
    """Geometry could not be built, measured, or checked."""


class MountError(GeometryError):
    """A catalog part could not be mounted. The part was not moved."""


class SketchPlaneError(GeometryError):
    """The sketch is attached to a face that is no longer in the model."""


class PartNotFound(AICADError):
    """No part with that name is in the current document."""


class DocumentError(AICADError):
    """The session file could not be read or written."""


class PlanError(AICADError):
    """A plan could not be changed, or modeling from it is still closed."""


class PlanBuildError(PlanError):
    """One approved build step failed. The previous model was not changed."""

    def __init__(
        self,
        message: str,
        *,
        step_id: str,
        part: str,
        index: int,
        count: int,
        progress: list,
    ):
        super().__init__(message)
        self.step_id = step_id
        self.part = part
        self.index = index
        self.count = count
        self.progress = progress
