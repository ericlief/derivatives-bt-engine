"""Correlation-aware greedy selection using combined subsystem forecasts."""

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
    symbol: str
    ann_dvol: float
    trade_sr: float
    combined_turnover: float
    econ_family: str = ""
    mkt_risk_vol_day: float | None = None

    @property
    def annual_trade_cost_sr(self) -> float:
        return self.trade_sr * self.combined_turnover


@dataclass(frozen=True)
class GreedySelectionConfig:
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
    @abstractmethod
    def weights(self, symbols: list[str], correlation: np.ndarray) -> dict[str, float]:
        """Return normalized nonnegative instrument weights."""


class EqualPortfolioWeights(PortfolioWeightPolicy):
    def weights(self, symbols: list[str], correlation: np.ndarray) -> dict[str, float]:
        del correlation
        return {symbol: 1.0 / len(symbols) for symbol in symbols} if symbols else {}


class HandcraftedPortfolioWeights(PortfolioWeightPolicy):
    """Small Carver-style recursive two-cluster risk-weighting policy."""

    def weights(self, symbols: list[str], correlation: np.ndarray) -> dict[str, float]:
        if not symbols:
            return {}
        raw = self._recursive(symbols, correlation)
        total = sum(raw.values())
        return {symbol: raw[symbol] / total for symbol in symbols}

    def _recursive(self, symbols: list[str], correlation: np.ndarray) -> dict[str, float]:
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
    symbols: tuple[str, ...]
    weights: dict[str, float]
    idm: float
    score: float
    feasible: bool
    reason: str
    details: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class GreedySelectionResult:
    selected: tuple[str, ...]
    final_score: PortfolioScore
    trials: tuple[dict[str, object], ...]


class GreedyInstrumentSelector:
    """Add the best feasible candidate until score falls below a threshold."""

    def __init__(
        self,
        *,
        config: GreedySelectionConfig | None = None,
        weight_policy: PortfolioWeightPolicy | None = None,
    ) -> None:
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
            annual_cost = candidate.annual_trade_cost_sr
            net_sr = self.config.gross_sr - annual_cost - size_penalty
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
        if not candidate_list:
            raise ValueError("at least one selection candidate is required")
        full_symbols = [candidate.symbol for candidate in candidate_list]
        if correlation.shape != (len(full_symbols), len(full_symbols)):
            raise ValueError("candidate correlation matrix has the wrong shape")
        candidates = {candidate.symbol: candidate for candidate in candidate_list}
        trials: list[dict[str, object]] = []

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
        for row in reversed(trials):
            if row["iteration"] == iteration and row["candidate"] == candidate:
                row["accepted"] = True
                return
