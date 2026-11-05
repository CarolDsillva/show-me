"""Fixtures for the security suite. It runs only on the VM, against real Docker and gVisor.

If the sandbox can't start, tests fail. They never skip, because a skipped security test
would look like a pass.
"""

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "sandboxd" / "spike"))

import sandbox as sb  # noqa: E402


@pytest.fixture(scope="session")
def image() -> str:
    value = os.environ.get("REPROFIX_IMAGE")
    if not value:
        pytest.fail("REPROFIX_IMAGE is not set. This suite must run on the VM through scripts/run-spike.sh.")
    return value


@pytest.fixture
def box(image):
    with sb.Sandbox(image) as sandbox:
        yield sandbox
    assert sandbox.destroyed
    assert not sb.exists(sandbox.run_id)


@pytest.fixture(scope="session", autouse=True)
def no_containers_left_behind():
    yield
    assert sb.labeled_containers() == []
