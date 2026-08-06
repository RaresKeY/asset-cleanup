"""Installed processor discovery without invoking untrusted recipe content."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from importlib import metadata


@dataclass(frozen=True, slots=True)
class Capability:
    """One optional or required processing capability."""

    name: str
    available: bool
    version: str | None
    provider: str
    executable: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _package(name: str, *, import_name: str | None = None, note: str | None = None) -> Capability:
    import_name = import_name or name.replace("-", "_")
    available = importlib.util.find_spec(import_name) is not None
    version: str | None = None
    if available:
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            version = "unknown"
    return Capability(name=name, available=available, version=version, provider="python", note=note)


def _executable(
    name: str,
    *,
    candidates: Iterable[str] | None = None,
    version_args: Iterable[str] | None = ("--version",),
    note: str | None = None,
) -> Capability:
    executable = next(
        (found for candidate in (candidates or (name,)) if (found := shutil.which(candidate))),
        None,
    )
    version: str | None = None
    if executable and version_args is not None:
        try:
            completed = subprocess.run(
                [executable, *version_args],
                capture_output=True,
                check=False,
                text=True,
                timeout=3,
            )
            version = (completed.stdout or completed.stderr).splitlines()[0][:200]
        except (OSError, subprocess.SubprocessError, IndexError):
            version = "available; version probe failed"
    return Capability(
        name=name,
        available=executable is not None,
        version=version,
        provider="executable",
        executable=executable,
        note=note,
    )


def detect_capabilities() -> list[Capability]:
    """Return a stable inventory of canonical and optional processors."""

    capabilities = [
        _package("numpy"),
        _package("scipy"),
        _package("trimesh"),
        _package("fast-simplification", import_name="fast_simplification"),
        _package("coacd", note="optional convex decomposition"),
        _package("fastapi", note="web extra"),
        _executable("gltfpack"),
        _executable("gltf-transform"),
        _executable(
            "gltf-validator",
            candidates=("gltf_validator", "gltf-validator"),
            version_args=None,
            note="version is verified from the JSON report during validation",
        ),
        _executable("blender", version_args=("--version",)),
        _executable("godot", version_args=("--version",)),
    ]
    return sorted(capabilities, key=lambda item: (item.provider, item.name))


def capability_map() -> dict[str, dict[str, object]]:
    """Return capabilities keyed by their stable names."""

    return {item.name: item.to_dict() for item in detect_capabilities()}
