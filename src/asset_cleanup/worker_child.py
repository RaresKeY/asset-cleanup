"""Resource-bounded internal subprocess entry point for one processing job."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from asset_cleanup.models import Recipe
from asset_cleanup.processing import run_pipeline


def _set_limit(kind: int, requested: int) -> None:
    import resource

    soft, hard = resource.getrlimit(kind)
    ceiling = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
    resource.setrlimit(kind, (ceiling, hard))


def apply_posix_limits(max_runtime_seconds: int, max_memory_bytes: int) -> None:
    """Apply supported POSIX limits before opening untrusted geometry."""

    if os.name != "posix":
        return
    import resource

    _set_limit(resource.RLIMIT_CPU, max(1, max_runtime_seconds))
    _set_limit(resource.RLIMIT_AS, max_memory_bytes)
    _set_limit(resource.RLIMIT_NOFILE, 256)


def apply_resource_limits(recipe: Recipe) -> None:
    """Apply the resource ceilings recorded by a processing recipe."""

    limits = recipe.settings.limits
    apply_posix_limits(limits.max_runtime_seconds, limits.max_memory_bytes)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("source", type=Path)
    parser.add_argument("recipe", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    recipe = Recipe.load(arguments.recipe)
    apply_resource_limits(recipe)
    run_pipeline(arguments.source, recipe, arguments.output)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through worker integration
    raise SystemExit(main())
