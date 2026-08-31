"""Fallback-chain reporting: expected attempts must not read as failures.

A refresh log that ends in success should not look like a crash. The optional
feeds try several providers in order and use whichever answers first, so one
source failing is the design working, not an error. Logging every attempt at the
same volume as a real failure made a fully successful refresh read as broken.

Separately, a hostname that will not resolve is a problem on THIS machine, not the
provider being down — those get classified and surfaced once with a fix hint,
rather than buried inside a urllib3 traceback.
"""
from __future__ import annotations

import pytest

from core import optional_data_fetchers as odf

GROWW_DNS = (
    "HTTPSConnectionPool(host='groww.in', port=443): Max retries exceeded with url: "
    "/v1/api/stocks_data/v1/accord_points/exchange/NSE/type/index/BSEIndex_fii_dii "
    "(Caused by NameResolutionError(\"<urllib3.connection.HTTPSConnection object at "
    "0x00000233EC07FAC0>: Failed to resolve 'groww.in' ([Errno 11002] getaddrinfo failed)\"))"
)


@pytest.mark.parametrize("exc,kind", [
    (ConnectionError(GROWW_DNS), "dns"),
    (RuntimeError("Could not resolve host: github.com"), "dns"),
    (RuntimeError("NSE historical FII/DII failed after retry: HTTP 503"), "blocked"),
    (RuntimeError("HTTP 429 too many requests"), "blocked"),
    (RuntimeError("no HTML parser succeeded: No tables found matching pattern '.+'"), "shape"),
    (RuntimeError("empty result"), "shape"),
    (TimeoutError("read timed out"), "network"),
    (ValueError("something else entirely"), "other"),
])
def test_source_errors_are_classified(exc, kind):
    got, message = odf._describe_source_error(exc)
    assert got == kind, f"{exc} -> {got}, expected {kind}"
    assert message and len(message) < 160, "message must stay short enough to read"


def test_dns_message_names_the_host():
    _, msg = odf._describe_source_error(ConnectionError(GROWW_DNS))
    assert "groww.in" in msg
    assert "DNS" in msg or "resolve" in msg


def test_dns_message_does_not_dump_the_traceback():
    """The raw urllib3 text is 300+ chars of noise including a memory address."""
    _, msg = odf._describe_source_error(ConnectionError(GROWW_DNS))
    assert "urllib3" not in msg
    assert "0x0000" not in msg
    assert len(msg) < len(GROWW_DNS) / 2


def test_attempt_is_logged_as_try_not_failure(capsys=None):
    import io
    import contextlib

    buf = io.StringIO()
    odf._DNS_FAILURES.clear()
    with contextlib.redirect_stdout(buf):
        odf._log_source_attempt("fii_dii", "groww", ConnectionError(GROWW_DNS))
    out = buf.getvalue()

    assert "[fetch][try]" in out, "an expected fallback attempt must not read as an error"
    assert "[warn]" not in out
    assert "unavailable" in out
    odf._DNS_FAILURES.clear()


def test_dns_failures_are_collected_for_the_end_of_run_note():
    odf._DNS_FAILURES.clear()
    import io
    import contextlib

    with contextlib.redirect_stdout(io.StringIO()):
        odf._log_source_attempt("fii_dii", "groww", ConnectionError(GROWW_DNS))
        odf._log_source_attempt("fii_dii", "nse-archive",
                                RuntimeError("HTTP 503"))

    assert "groww" in odf._DNS_FAILURES
    assert "nse-archive" not in odf._DNS_FAILURES, "a 503 is the source blocking us, not DNS"
    odf._DNS_FAILURES.clear()


def test_summary_reports_which_source_won():
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        odf._log_source_summary("fii_dii", ["nse-api"], ["nse-archive", "moneycontrol", "groww"])
    out = buf.getvalue()

    assert "ok via nse-api" in out
    assert "3 other source(s) unavailable" in out


def test_summary_is_silent_when_nothing_succeeded():
    """All-sources-failed is reported by the caller as a [warn], not here."""
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        odf._log_source_summary("fii_dii", [], ["a", "b"])
    assert buf.getvalue().strip() == ""


def test_all_sources_failing_is_still_a_warning():
    """Downgrading attempts must not downgrade a genuine total failure.

    Checked structurally (ast) rather than by log wording: the branch taken when
    nothing was collected must call _warn.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(odf.fetch_fii_dii)))

    def calls_warn(nodes) -> bool:
        return any(
            isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_warn"
            for node in nodes
            for n in ast.walk(node)
        )

    failure_branches = [
        stmt for stmt in ast.walk(tree)
        if isinstance(stmt, ast.If) and "collected" in ast.dump(stmt.test)
    ]
    assert failure_branches, "no all-sources-failed branch found in fetch_fii_dii"
    assert any(calls_warn(b.body) or calls_warn(b.orelse) for b in failure_branches), (
        "a feed with no working source must still warn")
