from typing import Any, Protocol


class Strategy(Protocol):
    NAME: str
    DEFAULT_HYPERPARAMS: dict[str, Any]

    @staticmethod
    def generate_signals(bars: list, hyperparams: dict[str, Any]) -> list[dict]:
        ...
