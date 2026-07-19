import os

import pytest
from anthropic import Anthropic


@pytest.mark.skipif(
    os.environ.get("ANTHROPIC_API_KEY") is None and not os.path.exists(
        "tests/cassettes/cassette_smoke.yaml"
    ),
    reason="no api key and no cassette",
)
def test_cassette_replay_smoke(vcr_cassette):
    with vcr_cassette("cassette_smoke"):
        # We don't make a live call here — we just verify the fixture mounts
        # without raising. If a cassette exists, anthropic can be constructed.
        c = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", "sk-fake"))
        assert c is not None
