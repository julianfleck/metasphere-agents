"""Regression tests for the installed distribution boundary."""

from __future__ import annotations

import tomllib
from pathlib import Path

from setuptools import find_packages


def test_package_discovery_contains_all_runtime_packages():
    """The wheel's discovery rules must include every runtime package."""
    repo = Path(__file__).resolve().parents[2]
    with (repo / "pyproject.toml").open("rb") as stream:
        config = tomllib.load(stream)["tool"]["setuptools"]["packages"]["find"]

    packages = set(
        find_packages(
            where=repo,
            include=config["include"],
            exclude=config["exclude"],
        )
    )

    assert "metasphere.slack" in packages
    assert "metasphere.routing" in packages
    assert "metasphere.gateway.adapters" in packages
    assert "metasphere.tests" not in packages
