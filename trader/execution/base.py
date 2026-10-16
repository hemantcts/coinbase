"""Broker interface. Paper and live brokers both implement it."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Order


class Broker(ABC):
    mode: str = "paper"

    @abstractmethod
    def submit(self, order: Order) -> Order:
        """Submit the order. Returns the order with its updated status/fill fields."""

    @abstractmethod
    def status(self, order: Order) -> Order:
        """Refresh the order from the venue."""

    @abstractmethod
    def cancel(self, order: Order) -> Order:
        """Cancel an open order."""

    def balances(self) -> dict[str, float]:
        """Available balances by currency. Paper broker has none."""
        return {}
