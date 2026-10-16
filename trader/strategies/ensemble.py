"""Ensemble: run all strategies, open on the best cost-adjusted BUY, close on the owner's SELL."""
from __future__ import annotations

from ..models import Candle, Position, Side, Signal
from .base import Strategy


class Ensemble(Strategy):
    name = "ensemble"

    def __init__(self, members: list[Strategy]):
        if not members:
            raise ValueError("Ensemble needs at least one strategy")
        self.members = members
        self.by_name = {m.name: m for m in members}
        self.warmup = max(m.warmup for m in members)

    def evaluate(self, product: str, candles: list[Candle], position: Position | None) -> Signal:
        if position is not None:
            owner = self.by_name.get(position.strategy)
            if owner is None:
                # Position opened by a strategy no longer configured: let any member close it.
                for m in self.members:
                    s = m.evaluate(product, candles, position)
                    if s.side == Side.SELL:
                        return s
                return self.hold(product, "orphan position, no exit yet")
            return owner.evaluate(product, candles, position)

        best: Signal | None = None
        for m in self.members:
            s = m.evaluate(product, candles, None)
            if s.side != Side.BUY:
                continue
            if best is None or (s.expected_move * s.confidence) > (best.expected_move * best.confidence):
                best = s
        return best or self.hold(product, "no member signals")
