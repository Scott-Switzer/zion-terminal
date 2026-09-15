"""Contract-only world routing for real and synthetic financial backends.

Zion owns planning and synthesis. Producers remain outside this repository and
communicate through the serialized FinancialQueryRequest/Response shape.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

REQUIRED_RESPONSE_FIELDS = {"world", "entity", "answer", "metrics", "calculations", "evidence", "quality"}


class WorldRouteError(ValueError):
    pass


WorldHandler = Callable[[dict[str, Any]], dict[str, Any]]


class WorldRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, WorldHandler] = {}

    def register(self, world_type: str, handler: WorldHandler) -> None:
        if world_type not in {"real", "synthetic"}:
            raise WorldRouteError(f"unsupported world_type: {world_type}")
        if world_type in self._handlers:
            raise WorldRouteError(f"world handler already registered: {world_type}")
        self._handlers[world_type] = handler

    def query(self, request: dict[str, Any]) -> dict[str, Any]:
        world = request.get("world")
        if not isinstance(world, dict) or world.get("world_type") not in {"real", "synthetic"}:
            raise WorldRouteError("invalid WorldRefV1")
        if not isinstance(request.get("query"), str) or not request["query"].strip():
            raise WorldRouteError("query is required")
        handler = self._handlers.get(world["world_type"])
        if handler is None:
            raise WorldRouteError(f"no handler registered for {world['world_type']}")
        response = handler(request)
        if not isinstance(response, dict) or not REQUIRED_RESPONSE_FIELDS.issubset(response):
            raise WorldRouteError("handler returned an invalid FinancialQueryResponseV1")
        response_world = response["world"]
        if response_world.get("world_type") != world["world_type"]:
            raise WorldRouteError("response world_type does not match request")
        if response_world.get("world_id") != world.get("world_id"):
            raise WorldRouteError("response world_id does not match request")
        return response
