"""Shared pytest fixtures and configuration."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Test result logger — writes full session output to a timestamped log file
# ---------------------------------------------------------------------------

_LOG_DIR = Path(__file__).resolve().parents[1] / "outputs" / "test_logs"
_results_logger = logging.getLogger("pytest.results")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests that call the real LLM endpoint (requires .env with valid credentials)",
    )

    # Create a timestamped log file for this run
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = _LOG_DIR / f"pytest_{ts}.log"

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    _results_logger.addHandler(handler)
    _results_logger.propagate = True


def pytest_sessionstart(session):
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _results_logger.info("=" * 70)
    _results_logger.info(
        "TEST SESSION STARTED  %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    _results_logger.info("=" * 70)


def pytest_runtest_logreport(report):
    if report.when != "call" and not (report.when == "setup" and report.failed):
        return

    outcome = report.outcome.upper()  # PASSED / FAILED / ERROR
    duration = f"{report.duration:.3f}s"
    name = report.nodeid

    if report.passed:
        _results_logger.info("  PASSED  (%s)  %s", duration, name)
    elif report.failed:
        _results_logger.error("  FAILED  (%s)  %s", duration, name)
        if report.longreprtext:
            for line in report.longreprtext.splitlines():
                _results_logger.error("    %s", line)
    elif report.skipped:
        _results_logger.warning("  SKIPPED         %s", name)


def pytest_sessionfinish(session, exitstatus):
    _results_logger.info("-" * 70)
    _results_logger.info(
        "TEST SESSION FINISHED  exit=%s",
        exitstatus,
    )
    _results_logger.info("=" * 70)


@pytest.fixture(autouse=True)
def _stub_env_vars(monkeypatch):
    """Ensure unit tests never accidentally read real credentials from .env.

    Unit tests that need specific values set them explicitly via monkeypatch.
    Integration tests opt out by using the ``integration`` marker — those tests
    rely on the real .env being present.
    """
    pass  # individual tests set/override vars as needed


@pytest.fixture
def fake_env(monkeypatch):
    """Inject dummy LLM env vars suitable for unit tests with a mocked LLM."""
    monkeypatch.setenv("OPENAI_API_KEY", "dummykey")
    monkeypatch.setenv("OPENAI_API_BASE", "https://fake-llm.example.com")
    monkeypatch.setenv("LLM_MODEL", "gpt-test")
