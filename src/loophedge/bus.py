import json
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel
from redis.asyncio import Redis

CH_BAR_CLOSED = "bar.closed"
CH_SIGNAL_CANDIDATE = "signal.candidate"
CH_SIGNAL_VERIFIED = "signal.verified"
CH_SIGNAL_REJECTED = "signal.rejected"
CH_CIRCUIT_BROKEN = "circuit.broken"


class Bus:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def publish(self, channel: str, payload: BaseModel) -> None:
        await self.redis.publish(channel, payload.model_dump_json())

    async def subscribe(self, channel: str) -> AsyncIterator[dict[str, Any]]:
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                yield json.loads(msg["data"])
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
