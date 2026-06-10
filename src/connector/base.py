"""Connector base — read-only data access contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Connector(ABC):
    """Read-only external data fetcher.

    Cannot write state. Cannot call interface.
    Only returns raw data from external systems.
    """

    @abstractmethod
    async def authenticate(self) -> bool:
        """Establish session with external system. Returns True on success."""
        ...

    @abstractmethod
    async def fetch(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch raw data. Returns unstructured dict."""
        ...

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Unique identifier for this data source."""
        ...
