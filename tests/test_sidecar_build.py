from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import build_release
from scripts.build_sidecar import render_constraints, write_manifest
from scripts.smoke_sidecar import default_binary

ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = ROOT / "scripts" / "smoke_sidecar.py"


def test_generated_constraints_pin_packages_and_exclude_local_readio_path() -> None:
    constraints = render_constraints(
        "setuptools==80.9.0\n"
        "readio @ file:///build/checkout/vendor/readio\n"
        "Pynput==1.7.7\n"
        "# generated comment\n"
    )
    assert constraints == "Pynput==1.7.7\nsetuptools==80.9.0\n"


def test_version_manifest_binds_versions_constraints_and_binary(tmp_path: Path) -> None:
    binary = tmp_path / "voxselect-readio-service-test"
    binary.write_bytes(b"frozen-sidecar")
    constraints = tmp_path / "constraints.txt"
    constraints.write_text("pynput==1.7.7\n", encoding="utf-8")
    manifest_path = tmp_path / "sidecar.manifest.json"

    manifest = write_manifest(
        path=manifest_path,
        target="test-target",
        engine_extra="none",
        python_version="3.13.1",
        package_versions={"Readio": "0.6.8", "PyInstaller": "6.18.0"},
        constraints_path=constraints,
        binary_path=binary,
    )

    assert manifest["schema"] == "voxselect.sidecar-build.v1"
    assert manifest["target_triple"] == "test-target"
    assert manifest["engine_extra"] == "none"
    assert manifest["readio_version"] == "0.6.8"
    assert manifest["binary"]["sha256"]
    assert manifest["constraints"]["sha256"]
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest


def test_smoke_script_has_explicit_skip_for_missing_target_binary(tmp_path: Path) -> None:
    missing = tmp_path / "missing-sidecar"
    result = subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT), "--binary", str(missing), "--skip-missing"],
        check=True,
        text=True,
        capture_output=True,
    )
    assert "SKIP: target frozen sidecar is unavailable" in result.stdout


def test_exact_target_frozen_sidecar_smoke() -> None:
    try:
        binary = default_binary()
    except (FileNotFoundError, subprocess.CalledProcessError, RuntimeError) as error:
        pytest.skip(f"target runner is unavailable: {error}")
    if not binary.is_file():
        pytest.skip(f"target frozen sidecar is not built: {binary}")
    if not Path(f"{binary}.manifest.json").is_file():
        pytest.skip("generated sidecar version manifest is not available")
    result = subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT), "--binary", str(binary)],
        check=True,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert "PASS:" in result.stdout



def test_release_stages_sidecar_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary_dir = tmp_path / "binaries"
    binary_dir.mkdir()
    target_name = "voxselect-readio-service-test-target"
    manifest = binary_dir / f"{target_name}.manifest.json"
    constraints = binary_dir / f"{target_name}-kitten.constraints.txt"
    manifest.write_text("{}\n", encoding="utf-8")
    constraints.write_text("readio==0.6.8\n", encoding="utf-8")
    monkeypatch.setattr(build_release, "SIDECAR_BINARIES", binary_dir)

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    records = build_release.stage_sidecar_metadata(
        system="linux",
        target="test-target",
        engine_extra="Kitten",
        downloads=downloads,
    )

    assert [record["file"] for record in records] == [
        manifest.name,
        constraints.name,
    ]
    assert (downloads / manifest.name).read_text(encoding="utf-8") == "{}\n"
    assert records[0]["sha256"] and records[1]["sha256"]
