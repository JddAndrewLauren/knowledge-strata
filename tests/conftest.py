"""A skipped test is a failed one (wayfinder #12).

The ordinary suite is hermetic, so nothing in it has a reason to skip. This
hook turns any skip into a failed run, named in ASCII on stdout, rather than
leaving the rule as a sentence in CLAUDE.md.
"""

import pytest

_skipped: list[str] = []


def pytest_runtest_logreport(report):
    if report.skipped:
        _skipped.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    if _skipped:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        print(f"\nFAILED: {len(_skipped)} skipped test(s); a skip is a failure: {', '.join(_skipped)}")
