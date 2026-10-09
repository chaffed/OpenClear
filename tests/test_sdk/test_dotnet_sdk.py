# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The .NET SDK (sdks/dotnet, SDK_PLAN.md P3) builds, with warnings as errors, and its tests
pass, in the official .NET 8 SDK container. Skipped without Docker or that image."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SDK = Path(__file__).resolve().parents[2] / "sdks" / "dotnet"
IMAGE = "mcr.microsoft.com/dotnet/sdk:8.0"


def _docker() -> str | None:
    docker = shutil.which("docker")
    if not docker:
        return None
    found = subprocess.run([docker, "image", "inspect", IMAGE], capture_output=True)
    return docker if found.returncode == 0 else None


DOCKER = _docker()


@pytest.mark.skipif(DOCKER is None, reason=f"needs Docker with {IMAGE}")
def test_the_dotnet_sdk_builds_and_its_tests_pass():
    result = subprocess.run(
        [DOCKER, "run", "--rm", "-v", f"{SDK}:/sdk", "-w", "/sdk/tests/OpenClear.Tests", IMAGE, "dotnet", "test", "--nologo", "-v", "q"],
        capture_output=True, text=True, timeout=900, env=os.environ,
    )
    assert result.returncode == 0 and "Passed!" in result.stdout, result.stdout[-4000:] + result.stderr[-2000:]
