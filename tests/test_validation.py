from __future__ import annotations

import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

from asset_cleanup.validation import (
    GLTF_VALIDATOR_MAX_ISSUES,
    GLTF_VALIDATOR_VERSION,
    run_gltf_validator,
)


def _fake_validator(tmp_path: Path, source: str) -> Path:
    executable = tmp_path / "fake-gltf-validator"
    executable.write_text(
        f"#!{sys.executable}\n{textwrap.dedent(source)}",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable


def _asset(tmp_path: Path) -> Path:
    asset = tmp_path / "asset with spaces.glb"
    asset.write_bytes(b"glTF")
    return asset


def _report_source(
    *,
    version: str = GLTF_VALIDATOR_VERSION,
    errors: int = 0,
    warnings: int = 0,
    returncode: int = 0,
) -> str:
    return f"""
    import json
    import sys

    print(json.dumps({{
        "validatorVersion": {version!r},
        "issues": {{
            "numErrors": {errors},
            "numWarnings": {warnings},
            "numInfos": 0,
            "numHints": 0,
            "messages": [],
        }},
    }}))
    raise SystemExit({returncode})
    """


def test_gltf_validator_uses_fixed_private_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DART_VM_OPTIONS", "must-not-cross-boundary")
    executable = _fake_validator(
        tmp_path,
        f"""
        import json
        import os
        import pathlib
        import stat
        import sys

        args = sys.argv[1:]
        config_index = args.index("--config") + 1
        config_path = pathlib.Path(args[config_index])
        report = {{
            "validatorVersion": {GLTF_VALIDATOR_VERSION!r},
            "issues": {{
                "numErrors": 0,
                "numWarnings": 0,
                "numInfos": 0,
                "numHints": 0,
                "messages": [],
            }},
            "_test": {{
                "argv": args,
                "config": config_path.read_text(encoding="utf-8"),
                "config_path": str(config_path),
                "config_mode": stat.S_IMODE(config_path.stat().st_mode),
                "directory_mode": stat.S_IMODE(config_path.parent.stat().st_mode),
                "cwd": os.getcwd(),
                "dart_options_present": "DART_VM_OPTIONS" in os.environ,
            }},
        }}
        print(json.dumps(report))
        """,
    )
    asset = _asset(tmp_path)

    result = run_gltf_validator(
        asset,
        str(executable),
        timeout_seconds=2,
        discovered_version=GLTF_VALIDATOR_VERSION,
    )

    assert result["ran"] is True
    assert result["passed"] is True
    assert result["timed_out"] is False
    assert result["report_overflow"] is False
    assert result["returncode"] == 0
    assert result["issue_counts"] == {
        "errors": 0,
        "warnings": 0,
        "infos": 0,
        "hints": 0,
    }
    assert result["argv_contract"] == [
        "<provider-executable>",
        "--stdout",
        "--validate-resources",
        "--no-write-timestamp",
        "--no-absolute-path",
        "--no-messages",
        "--config",
        "<private-config>",
        "<relative-candidate>",
    ]
    assert result["provider"] == {
        "name": "Khronos glTF Validator",
        "kind": "native-executable",
        "executable": executable.name,
        "expected_version": GLTF_VALIDATOR_VERSION,
        "discovered_version": GLTF_VALIDATOR_VERSION,
        "version": GLTF_VALIDATOR_VERSION,
        "version_match": True,
    }

    report = result["report"]
    assert report["_test"]["config"] == f"max-issues: {GLTF_VALIDATOR_MAX_ISSUES}\n"
    assert report["_test"]["config_mode"] == 0o600
    assert report["_test"]["directory_mode"] == 0o700
    assert report["_test"]["cwd"] == str(tmp_path)
    assert report["_test"]["dart_options_present"] is False
    argv = report["_test"]["argv"]
    assert argv == [
        "--stdout",
        "--validate-resources",
        "--no-write-timestamp",
        "--no-absolute-path",
        "--no-messages",
        "--config",
        report["_test"]["config_path"],
        f"./{asset.name}",
    ]
    assert not Path(report["_test"]["config_path"]).exists()


def test_gltf_validator_rejects_report_overflow(tmp_path: Path) -> None:
    executable = _fake_validator(
        tmp_path,
        """
        import sys
        sys.stdout.buffer.write(b"x" * 2048)
        sys.stdout.buffer.flush()
        """,
    )

    result = run_gltf_validator(
        _asset(tmp_path),
        str(executable),
        timeout_seconds=2,
        max_report_bytes=1_024,
    )

    assert result["ran"] is True
    assert result["passed"] is False
    assert result["report_overflow"] is True
    assert result["report"] is None
    assert result["report_bytes"] == 2_048
    assert "1024 byte limit" in result["reason"]


def test_gltf_validator_drains_and_bounds_stderr_tail(tmp_path: Path) -> None:
    marker = "END-OF-STDERR"
    executable = _fake_validator(
        tmp_path,
        f"""
        import json
        import sys

        sys.stderr.write("x" * 512)
        sys.stderr.write({marker!r})
        print(json.dumps({{
            "validatorVersion": {GLTF_VALIDATOR_VERSION!r},
            "issues": {{
                "numErrors": 0,
                "numWarnings": 0,
                "numInfos": 0,
                "numHints": 0,
                "messages": [],
            }},
        }}))
        """,
    )

    result = run_gltf_validator(
        _asset(tmp_path),
        str(executable),
        timeout_seconds=2,
        max_stderr_bytes=128,
    )

    assert result["passed"] is True
    assert result["stderr"].endswith(marker)
    assert len(result["stderr"].encode("utf-8")) <= 128
    assert result["stderr_bytes"] > 128
    assert result["stderr_truncated"] is True


@pytest.mark.skipif(os.name != "posix", reason="process-group assertion is POSIX-specific")
def test_gltf_validator_timeout_kills_descendants(tmp_path: Path) -> None:
    orphan_marker = tmp_path / "orphan-survived"
    ready_marker = tmp_path / "child-ready"
    executable = _fake_validator(
        tmp_path,
        f"""
        import pathlib
        import subprocess
        import sys
        import time

        subprocess.Popen([
            sys.executable,
            "-c",
            "import pathlib,signal,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "pathlib.Path({str(ready_marker)!r}).write_text('ready'); "
            "time.sleep(2); "
            "pathlib.Path({str(orphan_marker)!r}).write_text('survived')",
        ])
        for _ in range(100):
            if pathlib.Path({str(ready_marker)!r}).exists():
                break
            time.sleep(0.01)
        time.sleep(30)
        """,
    )

    started = time.monotonic()
    result = run_gltf_validator(
        _asset(tmp_path),
        str(executable),
        timeout_seconds=0.5,
    )
    elapsed = time.monotonic() - started
    time.sleep(1.2)

    assert elapsed < 5
    assert ready_marker.exists()
    assert result["ran"] is True
    assert result["passed"] is False
    assert result["timed_out"] is True
    assert "wall-time" in result["reason"]
    assert not orphan_marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="process-group assertion is POSIX-specific")
def test_gltf_validator_kills_pipe_holding_descendant_after_leader_exits(
    tmp_path: Path,
) -> None:
    orphan_marker = tmp_path / "pipe-holder-survived"
    ready_marker = tmp_path / "pipe-holder-ready"
    executable = _fake_validator(
        tmp_path,
        f"""
        import json
        import pathlib
        import subprocess
        import sys
        import time

        subprocess.Popen([
            sys.executable,
            "-c",
            "import pathlib,signal,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "pathlib.Path({str(ready_marker)!r}).write_text('ready'); "
            "time.sleep(2); "
            "pathlib.Path({str(orphan_marker)!r}).write_text('survived')",
        ])
        for _ in range(100):
            if pathlib.Path({str(ready_marker)!r}).exists():
                break
            time.sleep(0.01)
        print(json.dumps({{
            "validatorVersion": {GLTF_VALIDATOR_VERSION!r},
            "issues": {{
                "numErrors": 0,
                "numWarnings": 0,
                "numInfos": 0,
                "numHints": 0,
                "messages": [],
            }},
        }}))
        """,
    )

    started = time.monotonic()
    result = run_gltf_validator(_asset(tmp_path), str(executable), timeout_seconds=5)
    elapsed = time.monotonic() - started
    time.sleep(1.2)

    assert elapsed < 5
    assert ready_marker.exists()
    assert result["ran"] is True
    assert result["passed"] is False
    assert result["drain_incomplete"] is True
    assert result["reason"] == "validator output streams did not close"
    assert not orphan_marker.exists()


def test_gltf_validator_rejects_version_mismatch_but_retains_report(
    tmp_path: Path,
) -> None:
    executable = _fake_validator(
        tmp_path,
        _report_source(version="2.0.0-dev.older"),
    )

    result = run_gltf_validator(_asset(tmp_path), str(executable), timeout_seconds=2)

    assert result["ran"] is True
    assert result["passed"] is False
    assert result["report"] is not None
    assert result["provider"]["version"] == "2.0.0-dev.older"
    assert result["provider"]["version_match"] is False
    assert "version mismatch" in result["reason"]


def test_gltf_validator_preserves_warning_evidence(tmp_path: Path) -> None:
    executable = _fake_validator(
        tmp_path,
        _report_source(warnings=1),
    )

    result = run_gltf_validator(_asset(tmp_path), str(executable), timeout_seconds=2)

    assert result["passed"] is True
    assert result["warning_count"] == 1
    assert result["issue_counts"] == {
        "errors": 0,
        "warnings": 1,
        "infos": 0,
        "hints": 0,
    }


def test_gltf_validator_retains_error_report_and_fails_closed(tmp_path: Path) -> None:
    executable = _fake_validator(
        tmp_path,
        _report_source(errors=1, returncode=1),
    )

    result = run_gltf_validator(_asset(tmp_path), str(executable), timeout_seconds=2)

    assert result["ran"] is True
    assert result["passed"] is False
    assert result["returncode"] == 1
    assert result["issue_counts"]["errors"] == 1
    assert result["report"] is not None
    assert result["reason"] == "validator reported glTF errors"


@pytest.mark.parametrize(
    "source, expected_reason",
    [
        ("print('not-json')", "strict UTF-8 JSON"),
        (
            "import json; print(json.dumps({'validatorVersion': "
            + repr(GLTF_VALIDATOR_VERSION)
            + ", 'issues': {'numErrors': True}}))",
            "invalid issue summary",
        ),
    ],
)
def test_gltf_validator_rejects_invalid_reports(
    tmp_path: Path,
    source: str,
    expected_reason: str,
) -> None:
    executable = _fake_validator(tmp_path, source)

    result = run_gltf_validator(_asset(tmp_path), str(executable), timeout_seconds=2)

    assert result["passed"] is False
    assert expected_reason in result["reason"]


def test_gltf_validator_reports_spawn_failure(tmp_path: Path) -> None:
    result = run_gltf_validator(
        _asset(tmp_path),
        str(tmp_path / "missing-validator"),
        timeout_seconds=2,
    )

    assert result["ran"] is False
    assert result["passed"] is False
    assert result["returncode"] is None
    assert "execution failed" in result["reason"]
