"""Position sizing utilities (Kelly fraction, vol targeting)."""
from __future__ import annotations

from common.config import get_setting


class PositionSizer:
    def __init__(self) -> None:
        self.max_pos: float = float(get_setting("risk.max_position_weight", 0.10))

    def vol_target_weight(
        self, score: float, asset_vol: float, target_vol: float = 0.15
    ) -> float:
        """Volatility-targeted sizing with score tilt."""
        if asset_vol <= 0:
            return 0.0
        raw = (target_vol / asset_vol) * max(score, 0.0)
        return min(raw, self.max_pos)

    def kelly_fraction(self, win_prob: float, win_loss_ratio: float) -> float:
        """Fractional Kelly (1/4 Kelly for safety)."""
        if win_loss_ratio <= 0:
            return 0.0
        full = win_prob - (1 - win_prob) / win_loss_ratio
        return max(0.0, min(full * 0.25, self.max_pos))
