"""Shared pytest fixtures for the CampusGreen reliability suite.

All fixtures here point the CampusGreen data layer at an isolated copy of the
committed seed data (via ``CAMPUSGREEN_DATA_DIR``) and swap the module-global
issue store for one bound to that directory, so no test ever mutates the
committed ``data/issues.json``.
"""

import os
import tempfile
from pathlib import Path

import pytest

import test_helpers
import tool as campus_tool


@pytest.fixture(scope="session")
def isolated_data_dir() -> Path:
    target = Path(tempfile.mkdtemp(prefix="campusgreen-r5-data-"))
    test_helpers.seed_data_dir(target)
    os.environ["CAMPUSGREEN_DATA_DIR"] = str(target)
    yield target
    os.environ.pop("CAMPUSGREEN_DATA_DIR", None)
    os.environ.pop("CAMPUSGREEN_CHANNEL", None)


@pytest.fixture
def isolated_store(isolated_data_dir, monkeypatch) -> campus_tool.IssueStore:
    """Point the module-global issue store at a fresh, pristine copy of the seed data.

    The data directory is re-seeded on every test so exact-count assertions
    (e.g. sustainability reports over the seed set) are deterministic regardless
    of what earlier tests created.
    """
    test_helpers.seed_data_dir(isolated_data_dir)
    store = campus_tool.IssueStore()
    monkeypatch.setattr(campus_tool, "_ISSUE_STORE", store)
    return store
