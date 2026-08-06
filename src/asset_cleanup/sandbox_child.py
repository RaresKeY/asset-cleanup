"""Bounded subprocess entry point for web inspection and preview generation."""

from __future__ import annotations

import argparse
from pathlib import Path

from asset_cleanup.inspection import inspect_source
from asset_cleanup.source import LoadLimits, load_scene
from asset_cleanup.util import atomic_write_bytes, write_json
from asset_cleanup.validation import validate_source
from asset_cleanup.worker_child import apply_posix_limits


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("operation", choices=("inspect", "preview", "validate"))
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--max-file-bytes", type=int, required=True)
    parser.add_argument("--max-texture-pixels", type=int, required=True)
    parser.add_argument("--max-runtime-seconds", type=int, required=True)
    parser.add_argument("--max-memory-bytes", type=int, required=True)
    parser.add_argument("--max-samples", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=0)
    arguments = parser.parse_args()

    apply_posix_limits(arguments.max_runtime_seconds, arguments.max_memory_bytes)
    limits = LoadLimits(
        max_file_bytes=arguments.max_file_bytes,
        max_referenced_bytes=arguments.max_file_bytes,
        max_texture_pixels=arguments.max_texture_pixels,
    )
    if arguments.operation == "inspect":
        report = inspect_source(
            arguments.source,
            limits=limits,
            max_samples=arguments.max_samples,
            seed=arguments.seed,
        )
        write_json(arguments.output, report.to_dict())
    elif arguments.operation == "preview":
        scene = load_scene(arguments.source, limits)
        payload = scene.export(file_type="glb")
        if not isinstance(payload, bytes):
            raise RuntimeError("preview exporter did not return GLB bytes")
        atomic_write_bytes(arguments.output, payload)
    else:
        write_json(arguments.output, validate_source(arguments.source, limits=limits).to_dict())
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through web integration
    raise SystemExit(main())
