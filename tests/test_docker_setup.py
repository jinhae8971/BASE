"""Static validation of Dockerfile + docker-compose without actually building.

We can't run ``docker build`` in CI here, but we can catch the most common
breakage:

    - referenced files exist (COPY paths)
    - the entrypoint command resolves to an importable module
    - docker-compose volumes point at directories we own
    - .dockerignore is wired so the build context isn't accidentally huge
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_exists() -> None:
    assert (ROOT / "Dockerfile").exists()


def test_dockerignore_excludes_data_store() -> None:
    """data_store/ contains state + secrets-adjacent files, must NOT ship in
    the build context."""
    di = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "data_store" in di
    assert ".env" in di


def test_dockerfile_copy_paths_exist() -> None:
    """COPY pyproject.toml ./ — pyproject must exist at repo root."""
    df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY pyproject.toml" in df
    assert (ROOT / "pyproject.toml").exists()


def test_dockerfile_default_cmd_module_importable() -> None:
    """Default CMD points at scheduler.scheduler:main — file must exist."""
    df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "scheduler.scheduler" in df
    assert (ROOT / "src" / "scheduler" / "scheduler.py").exists()


def test_compose_services_have_required_keys() -> None:
    raw = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = raw.get("services") or {}
    assert "scheduler" in services
    assert "dashboard" in services
    for name, svc in services.items():
        assert "image" in svc or "build" in svc, f"{name}: no image/build"
        assert "command" in svc, f"{name}: no command"


def test_compose_volume_paths_exist() -> None:
    raw = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = raw.get("services") or {}
    for name, svc in services.items():
        for v in svc.get("volumes") or []:
            host = v.split(":")[0]
            if host.startswith("./"):
                p = ROOT / host[2:]
                # data_store may not exist yet (created at runtime) — only
                # check tracked dirs/files.
                if host[2:] in ("data_store", ".kis_token.json"):
                    continue
                assert p.exists(), f"{name}: volume host path {host} missing"


def test_dockerfile_uses_kst_timezone() -> None:
    """Korean trading hours rely on the container being in KST."""
    df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "Asia/Seoul" in df
