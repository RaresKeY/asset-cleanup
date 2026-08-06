"""Project-specific errors with stable user-facing categories."""

from __future__ import annotations


class AssetCleanupError(Exception):
    """Base class for expected application errors."""


class InputRejectedError(AssetCleanupError):
    """The source violates a format, safety, or capability boundary."""


class CapabilityError(AssetCleanupError):
    """A requested processor is unavailable in the current environment."""


class RecipeError(AssetCleanupError):
    """A recipe is invalid for the selected source or installed capabilities."""


class ProcessingError(AssetCleanupError):
    """A processing stage failed without corrupting an accepted candidate."""


class ValidationFailure(AssetCleanupError):
    """A candidate failed a required validation gate."""
