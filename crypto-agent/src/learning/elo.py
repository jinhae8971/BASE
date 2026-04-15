"""Agent ELO rating.

Each of the five signal-producing agents is scored on its monthly Sharpe
contribution inside a shadow portfolio. The ELO updater turns that into a
relative rating which becomes the weight in `portfolio.aggregator.aggregate`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.portfolio.aggregator import SIGNAL_AGENTS

K_FACTOR = 32.0
DEFAULT_RATING = 1500.0


@dataclass
class EloTable:
    ratings: dict[str, float] = field(
        default_factory=lambda: {a: DEFAULT_RATING for a in SIGNAL_AGENTS}
    )

    def weights(self) -> dict[str, float]:
        """Softmax-style conversion from ratings to aggregator weights."""
        import math

        scaled = {a: math.exp((r - DEFAULT_RATING) / 400.0) for a, r in self.ratings.items()}
        z = sum(scaled.values()) or 1.0
        return {a: v / z for a, v in scaled.items()}

    def update_pair(self, winner: str, loser: str) -> None:
        rw, rl = self.ratings[winner], self.ratings[loser]
        ew = 1.0 / (1.0 + 10 ** ((rl - rw) / 400.0))
        self.ratings[winner] = rw + K_FACTOR * (1 - ew)
        self.ratings[loser] = rl + K_FACTOR * (0 - (1 - ew))

    def update_from_scores(self, monthly_sharpe: dict[str, float]) -> None:
        """Pairwise updates: higher Sharpe beats lower Sharpe."""
        agents = [a for a in SIGNAL_AGENTS if a in monthly_sharpe]
        for i in range(len(agents)):
            for j in range(i + 1, len(agents)):
                a, b = agents[i], agents[j]
                sa, sb = monthly_sharpe[a], monthly_sharpe[b]
                if sa == sb:
                    continue
                winner, loser = (a, b) if sa > sb else (b, a)
                self.update_pair(winner, loser)
