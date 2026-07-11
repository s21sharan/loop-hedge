import pytest

from loophedge.cli import main


def test_unknown_subcommand_returns_nonzero():
    assert main(["nope"]) != 0


def test_dashboard_subcommand_dispatch(monkeypatch):
    called = {}
    def fake_run_dashboard():
        called["yes"] = True
    monkeypatch.setattr("loophedge.cli.run_dashboard", fake_run_dashboard)
    assert main(["dashboard"]) == 0
    assert called == {"yes": True}


def test_ingest_subcommand_dispatch(monkeypatch):
    called = {}
    def fake_run_ingest():
        called["yes"] = True
    monkeypatch.setattr("loophedge.cli.run_ingest", fake_run_ingest)
    assert main(["ingest"]) == 0
    assert called == {"yes": True}
