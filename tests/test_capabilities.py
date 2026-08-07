from __future__ import annotations

import asset_cleanup.capabilities as capabilities


def test_gltf_validator_discovery_skips_unsupported_version_probe(
    monkeypatch,
) -> None:
    def fake_which(candidate: str) -> str | None:
        return "/opt/gltf_validator" if candidate == "gltf_validator" else None

    def fail_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("gltf_validator discovery must not execute --version")

    monkeypatch.setattr(capabilities.shutil, "which", fake_which)
    monkeypatch.setattr(capabilities.subprocess, "run", fail_run)

    discovered = {
        item.name: item for item in capabilities.detect_capabilities()
    }["gltf-validator"]

    assert discovered.available is True
    assert discovered.executable == "/opt/gltf_validator"
    assert discovered.version is None
    assert discovered.note == "version is verified from the JSON report during validation"
