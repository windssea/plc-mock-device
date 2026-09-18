"""Common protocol adapter contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


@dataclass(slots=True)
class AdapterStatus:
    name: str
    running: bool = False
    connected: bool = False
    last_error: str | None = None
    last_transition_at: str | None = None
    dropped_messages: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Adapter(ABC):
    """Lifecycle and sample-consumer interface shared by all protocols."""

    name = "adapter"

    def __init__(self) -> None:
        self._status = AdapterStatus(name=self.name)

    @property
    def running(self) -> bool:
        return self._status.running

    @property
    def status(self) -> AdapterStatus:
        return self._status

    def status_dict(self) -> dict[str, Any]:
        return self._status.as_dict()

    def _transition(self, *, running: bool | None = None, connected: bool | None = None) -> None:
        if running is not None:
            self._status.running = running
        if connected is not None:
            self._status.connected = connected
        self._status.last_transition_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @abstractmethod
    async def start(self, config: Any, devices: Iterable[Any] | None = None) -> None:
        """Start the adapter and expose all configured devices."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the adapter. Calling it repeatedly must be safe."""

    @abstractmethod
    async def update(self, sample: Any) -> None:
        """Consume one generated sample."""

    async def reconfigure(self, config: Any, devices: Iterable[Any] | None = None) -> None:
        await self.stop()
        await self.start(config, devices)

    async def __aenter__(self) -> "Adapter":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.stop()

