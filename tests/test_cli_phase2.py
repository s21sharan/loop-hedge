from loophedge.cli import _COMMANDS, main


def test_new_commands_registered():
    for cmd in ("maker", "checker", "genesis"):
        assert cmd in _COMMANDS


def test_maker_dispatch(monkeypatch):
    called = {}
    monkeypatch.setattr("loophedge.cli.run_maker", lambda: called.setdefault("yes", True))
    assert main(["maker"]) == 0
    assert called == {"yes": True}


def test_checker_dispatch(monkeypatch):
    called = {}
    monkeypatch.setattr("loophedge.cli.run_checker", lambda: called.setdefault("yes", True))
    assert main(["checker"]) == 0
    assert called == {"yes": True}


def test_genesis_dispatch(monkeypatch):
    called = {}
    monkeypatch.setattr("loophedge.cli.run_genesis", lambda: called.setdefault("yes", True))
    assert main(["genesis"]) == 0
    assert called == {"yes": True}
