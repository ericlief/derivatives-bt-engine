"""Score and greedily select executable futures instruments for Phase 2.

Inputs are deliberately compact: one :class:`SelectionCandidate` per
executable symbol plus a same-order correlation matrix estimated from those
symbols' combined subsystem returns. Each trial rebuilds portfolio weights and
IDM, converts capital allocation to a maximum contract position, applies size
and liquidity constraints, and calculates the AFTS portfolio score.

This module does not build forecasts or query market data. Those operations
belong to ``futures_selection_phase2`` and ``forecast_combination``.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
from scipy.cluster import hierarchy as sch
from scipy.spatial import distance

from derivatives_bt_engine.domain.allocation import compute_idm


@dataclass(frozen=True)
class SelectionCandidate:
    """Store fixed inputs for one executable contract in every greedy trial.

    ``ann_dvol`` is annual USD volatility per contract. ``trade_sr`` is the
    one-way cost in SR units, while ``combined_turnover`` is the measured
    annual turnover of this symbol's combined subsystem. ``econ_family``
    prevents selecting duplicate execution routes for the same exposure.
    ``mkt_risk_vol_day`` is daily market volume expressed in annualized USD
    risk units, matching the Phase 2 participation calculation.
    """

    symbol: str
    ann_dvol: float
    trade_sr: float
    combined_turnover: float
    econ_family: str = ""
    mkt_risk_vol_day: float | None = None

    @property
    def annual_trade_cost_sr(self) -> float:
        """Return one-way trade SR multiplied by combined annual turnover."""
        return self.trade_sr * self.combined_turnover


@dataclass(frozen=True)
class GreedySelectionConfig:
    """Configure AFTS scoring, position granularity, and liquidity limits.

    ``starting_weight`` is used only when comparing singleton seeds. Later
    trials use correlation-derived portfolio weights. ``forecast_cap_ratio``
    is maximum forecast divided by average absolute forecast (20/10 = 2 in
    conventional Carver units). ``min_max_position`` enforces the half-contract
    granularity rule. ``score_threshold`` controls the tolerated decline from
    the best score observed so far.
    """

    capital: float = 100_000.0
    target_vol: float = 0.20
    gross_sr: float = 0.50
    score_threshold: float = 0.90
    starting_weight: float = 0.20
    max_idm: float = 2.5
    forecast_cap_ratio: float = 2.0
    min_max_position: float = 0.5
    size_penalty_scale: float = 0.125
    liquidity_days: int = 250
    max_pct_market_volume: float = 1.0

    def __post_init__(self) -> None:
        """Validate configuration ranges before any trial is evaluated."""
        if self.capital <= 0 or self.target_vol <= 0:
            raise ValueError("capital and target_vol must be positive")
        if not 0 <= self.score_threshold <= 1:
            raise ValueError("score_threshold must be between zero and one")
        if not 0 < self.starting_weight <= 1:
            raise ValueError("starting_weight must be in (0, 1]")
        if self.max_idm < 1 or self.forecast_cap_ratio <= 0:
            raise ValueError("max_idm and forecast_cap_ratio are invalid")
        if self.min_max_position <= 0 or self.size_penalty_scale < 0:
            raise ValueError("position and penalty parameters are invalid")
        if self.liquidity_days <= 0 or self.max_pct_market_volume <= 0:
            raise ValueError("liquidity parameters must be positive")


class PortfolioWeightPolicy(ABC):
    """Map a trial book and its correlation block to risk weights."""

    @abstractmethod
    def weights(self, symbols: list[str], correlation: np.ndarray) -> dict[str, float]:
        """Return normalized nonnegative instrument weights."""


class EqualPortfolioWeights(PortfolioWeightPolicy):
    """Assign the same risk weight to every instrument in a trial book."""

    def weights(self, symbols: list[str], correlation: np.ndarray) -> dict[str, float]:
        """Return ``1/n`` weights; ``correlation`` is intentionally unused."""
        del correlation
        return {symbol: 1.0 / len(symbols) for symbol in symbols} if symbols else {}


class HandcraftedPortfolioWeights(PortfolioWeightPolicy):
    """Implement Carver-style recursive two-cluster risk weighting.

    Instruments are split into two correlation clusters. Each cluster receives
    half the parent allocation after adjustment for its internal IDM, and
    clusters larger than two instruments are split recursively. Final raw
    weights are normalized to one.
    """

    def weights(self, symbols: list[str], correlation: np.ndarray) -> dict[str, float]:
        """Return normalized handcrafted weights for ``symbols`` in matrix order."""
        if not symbols:
            return {}
        raw = self._recursive(symbols, correlation)
        total = sum(raw.values())
        return {symbol: raw[symbol] / total for symbol in symbols}

    def _recursive(self, symbols: list[str], correlation: np.ndarray) -> dict[str, float]:
        """Recursively split a correlation block into two sub-portfolios."""
        if len(symbols) <= 2:
            return {symbol: 1.0 / len(symbols) for symbol in symbols}
        try:
            link = sch.linkage(distance.pdist(correlation), method="complete")
            cutoff = link[len(symbols) - 2][2] - 1e-6
            labels = sch.fcluster(link, cutoff, criterion="distance")
            if len(set(labels)) != 2:
                raise ValueError("clustering did not produce two groups")
        except (ValueError, FloatingPointError):
            labels = np.array([(idx % 2) + 1 for idx in range(len(symbols))])

        result: dict[str, float] = {}
        for label in sorted(set(labels)):
            indices = [idx for idx, value in enumerate(labels) if value == label]
            group_symbols = [symbols[idx] for idx in indices]
            group_correlation = correlation[np.ix_(indices, indices)]
            group_weights = self._recursive(group_symbols, group_correlation)
            group_idm = compute_idm(
                group_symbols, H=group_correlation, weights=group_weights
            )
            for symbol, weight in group_weights.items():
                result[symbol] = 0.5 * group_idm * weight
        return result


@dataclass(frozen=True)
class PortfolioScore:
    """Describe one complete trial book and all instrument-level diagnostics.

    ``score`` is negative infinity when any member violates a hard size or
    liquidity constraint. ``details`` retains the calculated position, cost,
    penalty, risk traded, and participation for every member.
    """

    symbols: tuple[str, ...]
    weights: dict[str, float]
    idm: float
    score: float
    feasible: bool
    reason: str
    details: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class GreedySelectionResult:
    """Return the accepted symbol order, final score, and every trial row."""

    selected: tuple[str, ...]
    final_score: PortfolioScore
    trials: tuple[dict[str, object], ...]


class GreedyInstrumentSelector:
    """Add the best feasible candidate until score falls below tolerance.

    The selector first scores every singleton using ``starting_weight`` and
    IDM=1. It then tries every unused economic family as the next addition,
    fully rescoring the hypothetical book. The first two-instrument book sets
    the score benchmark; later winners must remain above
    ``score_threshold * best_seen``.
    """

    def __init__(
        self,
        *,
        config: GreedySelectionConfig | None = None,
        weight_policy: PortfolioWeightPolicy | None = None,
    ) -> None:
        """Initialize the selector with configurable scoring and weight policies."""
        self.config = config or GreedySelectionConfig()
        self.weight_policy = weight_policy or HandcraftedPortfolioWeights()

    def score(
        self,
        symbols: list[str],
        candidates: dict[str, SelectionCandidate],
        full_symbols: list[str],
        full_correlation: np.ndarray,
        *,
        starting: bool = False,
    ) -> PortfolioScore:
        """Score one hypothetical book using its correlation submatrix.

        Parameters
        ----------
        symbols
            Executable symbols in the hypothetical book.
        candidates
            Lookup containing fixed cost, risk, and liquidity inputs.
        full_symbols
            Row/column order of ``full_correlation``.
        full_correlation
            Correlation matrix for the complete eligible universe.
        starting
            Use the configured singleton weight and IDM=1 for seed comparison.

        Returns
        -------
        PortfolioScore
            Complete score plus per-instrument constraint diagnostics.
        """
        # Slice the precomputed universe matrix into this trial book's order.
        indices = [full_symbols.index(symbol) for symbol in symbols]
        correlation = full_correlation[np.ix_(indices, indices)]
        weights = self.weight_policy.weights(symbols, correlation)
        if starting and len(symbols) == 1:
            weights_for_limits = {symbols[0]: self.config.starting_weight}
            idm = 1.0
        else:
            weights_for_limits = weights
            idm = min(
                self.config.max_idm,
                compute_idm(symbols, H=correlation, weights=weights),
            )

        details: list[dict[str, object]] = []
        feasible = True
        reasons: list[str] = []
        net_sharpes: dict[str, float] = {}
        for symbol in symbols:
            candidate = candidates[symbol]
            allocation = weights_for_limits[symbol]
            # Maximum contracts at the forecast cap. A value below 0.5 cannot
            # reliably express even one signed contract after rounding.
            max_position = (
                self.config.capital
                * self.config.target_vol
                * allocation
                * idm
                * self.config.forecast_cap_ratio
                / candidate.ann_dvol
            )
            size_penalty = (
                math.inf
                if max_position < self.config.min_max_position
                else self.config.size_penalty_scale / max_position**2
            )
            # AFTS assumes a common gross SR and subtracts each instrument's
            # own combined-forecast trading cost and granularity penalty.
            annual_cost = candidate.annual_trade_cost_sr
            net_sr = self.config.gross_sr - annual_cost - size_penalty
            # Translate trial risk allocation and measured turnover into the
            # same annualized-dollar-risk units as market volume.
            risk_traded_day = (
                self.config.capital
                * self.config.target_vol
                * allocation
                * idm
                * candidate.combined_turnover
                / self.config.liquidity_days
            )
            pct_market = (
                100.0 * risk_traded_day / candidate.mkt_risk_vol_day
                if candidate.mkt_risk_vol_day is not None
                and candidate.mkt_risk_vol_day > 0
                else None
            )
            reason = ""
            if not math.isfinite(size_penalty):
                reason = "max_position_below_half_contract"
            elif pct_market is None:
                reason = "market_risk_volume_unavailable"
            elif pct_market > self.config.max_pct_market_volume:
                reason = "market_participation_limit"
            if reason:
                feasible = False
                reasons.append(f"{symbol}:{reason}")
            net_sharpes[symbol] = net_sr
            details.append({
                "symbol": symbol,
                "weight": weights[symbol],
                "limit_weight": allocation,
                "ann_trade_cost_sr": annual_cost,
                "max_position": max_position,
                "size_penalty_sr": size_penalty,
                "net_sr": net_sr,
                "risk_traded_day": risk_traded_day,
                "pct_mkt_volume": pct_market,
                "reason": reason,
            })

        # Portfolio score rewards diversification through the same w'Hw risk
        # denominator used by IDM, but applies each member's net SR in the
        # numerator before deciding whether the candidate improves the book.
        vector = np.array([weights[symbol] for symbol in symbols])
        denominator = math.sqrt(max(float(vector @ correlation @ vector), 0.0))
        numerator = sum(weights[symbol] * net_sharpes[symbol] for symbol in symbols)
        score = numerator / denominator if denominator > 0 and feasible else -math.inf
        return PortfolioScore(
            tuple(symbols), weights, idm, score, feasible, ";".join(reasons), tuple(details)
        )

    def select(
        self,
        candidate_list: list[SelectionCandidate],
        correlation: np.ndarray,
    ) -> GreedySelectionResult:
        """Run the deterministic singleton seed and iterative addition loop.

        ``candidate_list`` order defines the correlation-matrix order.
        Economic-family duplicates are removed from consideration once one
        family member is selected. Every attempted addition is retained in the
        returned trial audit, including infeasible and rejected candidates.
        """
        if not candidate_list:
            raise ValueError("at least one selection candidate is required")
        full_symbols = [candidate.symbol for candidate in candidate_list]
        if correlation.shape != (len(full_symbols), len(full_symbols)):
            raise ValueError("candidate correlation matrix has the wrong shape")
        candidates = {candidate.symbol: candidate for candidate in candidate_list}
        trials: list[dict[str, object]] = []

        # Seed selection uses a conservative nominal portfolio weight rather
        # than pretending a one-market account permanently holds 100% weight.
        singleton_scores = []
        for symbol in full_symbols:
            scored = self.score(
                [symbol], candidates, full_symbols, correlation, starting=True
            )
            singleton_scores.append(scored)
            trials.append(self._trial_row(0, symbol, scored, accepted=False))
        feasible_singletons = [score for score in singleton_scores if score.feasible]
        if not feasible_singletons:
            raise ValueError("no candidate passed singleton granularity and liquidity")
        current = max(feasible_singletons, key=lambda item: (item.score, item.symbols[0]))
        selected = list(current.symbols)
        # Match the published AFTS loop: singleton scoring chooses the seed,
        # while the first n+1 book establishes the tolerance benchmark.
        best_seen = 0.0
        self._mark_accepted(trials, 0, selected[-1])

        iteration = 1
        while True:
            # Once one execution route is selected, alternatives borrowing the
            # same economic exposure cannot enter the book as fake breadth.
            selected_families = {
                candidates[symbol].econ_family for symbol in selected
                if candidates[symbol].econ_family
            }
            remaining = [
                symbol for symbol in full_symbols
                if symbol not in selected
                and (
                    not candidates[symbol].econ_family
                    or candidates[symbol].econ_family not in selected_families
                )
            ]
            if not remaining:
                break
            # Recalculate weights, IDM, positions, liquidity, costs, and score
            # for the entire hypothetical book for every possible addition.
            scored_trials = []
            for symbol in remaining:
                scored = self.score(
                    selected + [symbol], candidates, full_symbols, correlation
                )
                scored_trials.append((symbol, scored))
                trials.append(self._trial_row(iteration, symbol, scored, accepted=False))
            feasible = [item for item in scored_trials if item[1].feasible]
            if not feasible:
                break
            winner, winning_score = max(
                feasible, key=lambda item: (item[1].score, item[0])
            )
            if winning_score.score < self.config.score_threshold * best_seen:
                break
            selected.append(winner)
            current = winning_score
            best_seen = max(best_seen, winning_score.score)
            self._mark_accepted(trials, iteration, winner)
            iteration += 1

        return GreedySelectionResult(tuple(selected), current, tuple(trials))

    @staticmethod
    def _trial_row(
        iteration: int,
        candidate: str,
        score: PortfolioScore,
        *,
        accepted: bool,
    ) -> dict[str, object]:
        """Flatten a portfolio score into one persisted candidate-trial row."""
        return {
            "iteration": iteration,
            "candidate": candidate,
            "book": ",".join(score.symbols),
            "score": score.score if math.isfinite(score.score) else None,
            "idm": score.idm,
            "feasible": score.feasible,
            "accepted": accepted,
            "reason": score.reason,
        }

    @staticmethod
    def _mark_accepted(
        trials: list[dict[str, object]], iteration: int, candidate: str
    ) -> None:
        """Mark the previously recorded winning row for one iteration."""
        for row in reversed(trials):
            if row["iteration"] == iteration and row["candidate"] == candidate:
                row["accepted"] = True
                return
