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

HANDCRAFT_MAX_DIVERSIFICATION_MULTIPLIER = 2.5


def _handcrafted_diversification_multiplier(
    symbols: list[str],
    correlation: np.ndarray,
    weights: dict[str, float],
    *,
    maximum: float = HANDCRAFT_MAX_DIVERSIFICATION_MULTIPLIER,
) -> float:
    """Return pysystemtrade's single-period handcrafting multiplier.

    Unlike the production allocation layer's conservative IDM, this exact
    AFTS/pysystemtrade calculation retains signed correlations and does not
    normalize ``weights`` internally. Recursive child portfolios deliberately
    pass raw weights whose scale carries the diversification adjustment from
    lower tree levels. Normalizing them here changes all ancestor weights.

    Parameters
    ----------
    symbols
        Matrix-order symbols represented by ``correlation`` and ``weights``.
    correlation
        Same-order instrument correlation matrix.
    weights
        Raw or normalized risk weights for the current tree node.
    maximum
        Upper bound for the diversification multiplier; pysystemtrade uses
        2.5 in both recursive handcrafting and final portfolio sizing.

    Returns
    -------
    float
        ``min(maximum, 1 / sqrt(w' H w))`` or one when risk is unusable.
    """
    vector = np.array([weights[symbol] for symbol in symbols], dtype=float)
    variance = float(vector @ correlation @ vector)
    if not math.isfinite(variance) or variance < 1e-14:
        return 1.0
    return min(maximum, 1.0 / math.sqrt(variance))


@dataclass(frozen=True)
class SelectionCandidate:
    """Store fixed inputs for one executable contract in every greedy trial.

    ``ann_dvol`` is annual USD volatility per contract. ``trade_sr`` is the
    one-way ordinary-trade cost in SR units, while ``combined_turnover`` is the
    measured annual turnover of this symbol's combined subsystem. ``roll_sr``
    is the cost of replacing an old contract with a new one and ``ann_rolls``
    is the reference policy frequency. ``asset_class`` and
    ``economic_subclass`` locate the instrument in the reviewed allocation
    tree, while ``econ_family`` prevents selecting duplicate routes for the
    same exposure.
    ``mkt_risk_vol_day`` is daily market volume expressed in annualized USD
    risk units, matching the Phase 2 participation calculation.
    ``initial_margin_usd`` is the larger BUY/SELL one-contract marginal
    initial-margin change from IB and supports an optional capital-usage
    approximation.
    """

    symbol: str
    ann_dvol: float
    trade_sr: float
    combined_turnover: float
    asset_class: str
    economic_subclass: str
    econ_family: str = ""
    mkt_risk_vol_day: float | None = None
    initial_margin_usd: float | None = None
    roll_sr: float = 0.0
    ann_rolls: float = 0.0

    @property
    def annual_trade_cost_sr(self) -> float:
        """Return one-way trade SR multiplied by combined annual turnover."""
        return self.trade_sr * self.combined_turnover

    @property
    def annual_roll_cost_sr(self) -> float:
        """Return complete-roll SR multiplied by policy rolls per year."""
        return self.roll_sr * self.ann_rolls

    @property
    def annual_cost_sr(self) -> float:
        """Return annual ordinary-trade plus holding-roll cost in SR units."""
        return self.annual_trade_cost_sr + self.annual_roll_cost_sr

    @property
    def trade_cost_usd_per_con(self) -> float:
        """Return the current one-way ordinary-trade cost per contract."""
        return self.trade_sr * self.ann_dvol

    @property
    def roll_cost_usd_per_con(self) -> float:
        """Return the current complete-roll cost per held contract."""
        return self.roll_sr * self.ann_dvol


@dataclass(frozen=True)
class GreedySelectionConfig:
    """Configure AFTS scoring, position granularity, and liquidity limits.

    ``max_weight_idm`` caps the product of an instrument's risk weight and the
    trial-book IDM when calculating position size, liquidity usage, and margin.
    This is Carver's small-account convention: the same cap ranks singleton
    seeds and prevents a two-market trial from suddenly resizing both markets
    to half of the diversified book. Portfolio score and IDM still use the
    uncapped structural weights. ``forecast_cap_ratio`` is maximum forecast
    divided by average absolute forecast (20/10 = 2 in conventional Carver
    units). ``min_max_position`` enforces the half-contract granularity rule.
    ``max_instruments_per_cluster`` optionally limits the number of selected
    instruments sharing one economic subclass, such as ``grains``.
    ``score_threshold`` controls the tolerated decline from the best score
    observed so far. ``include_roll_cost_in_score`` changes the greedy net-SR
    deduction from ordinary trading cost alone to ordinary trading plus the
    reference holding-roll allowance. ``max_instrument_margin_pct`` optionally
    limits any one instrument's rounded maximum-position margin as a percentage
    of initial capital. When that gate is enabled, the cumulative book margin
    defaults to 100% of initial capital; ``max_book_margin_pct`` can set a
    different book ceiling. Both continuous and rounded estimates are retained
    for comparison. These are additive screens: they deliberately do not
    assume cross-product SPAN offsets, but remain estimates because IB's
    one-lot margin change depends on the account's positions at snapshot time.
    """

    capital: float = 100_000.0
    target_vol: float = 0.20
    gross_sr: float = 0.50
    score_threshold: float = 0.90
    max_weight_idm: float = 0.20
    max_idm: float = 2.5
    forecast_cap_ratio: float = 2.0
    min_max_position: float = 0.5
    size_penalty_scale: float = 0.125
    liquidity_days: int = 250
    max_pct_market_volume: float = 1.0
    max_instruments_per_cluster: int | None = None
    max_instrument_margin_pct: float | None = None
    max_book_margin_pct: float | None = None
    include_roll_cost_in_score: bool = False

    def __post_init__(self) -> None:
        """Validate configuration ranges before any trial is evaluated."""
        if self.capital <= 0 or self.target_vol <= 0:
            raise ValueError("capital and target_vol must be positive")
        if not 0 <= self.score_threshold <= 1:
            raise ValueError("score_threshold must be between zero and one")
        if not 0 < self.max_weight_idm <= 1:
            raise ValueError("max_weight_idm must be in (0, 1]")
        if self.max_idm < 1 or self.forecast_cap_ratio <= 0:
            raise ValueError("max_idm and forecast_cap_ratio are invalid")
        if self.min_max_position <= 0 or self.size_penalty_scale < 0:
            raise ValueError("position and penalty parameters are invalid")
        if self.liquidity_days <= 0 or self.max_pct_market_volume <= 0:
            raise ValueError("liquidity parameters must be positive")
        if (
            self.max_instruments_per_cluster is not None
            and self.max_instruments_per_cluster <= 0
        ):
            raise ValueError(
                "max_instruments_per_cluster must be positive when set"
            )
        if (
            self.max_instrument_margin_pct is not None
            and not 0 < self.max_instrument_margin_pct <= 100
        ):
            raise ValueError(
                "max_instrument_margin_pct must be in (0, 100] when set"
            )
        if (
            self.max_book_margin_pct is not None
            and not 0 < self.max_book_margin_pct <= 100
        ):
            raise ValueError("max_book_margin_pct must be in (0, 100] when set")


@dataclass(frozen=True)
class PortfolioWeightAllocation:
    """Return portfolio weights together with their hierarchical grouping.

    ``weights`` maps each symbol to its normalized risk weight. The default
    ``cluster_paths`` values are readable classification paths such as
    ``ags/grains``. The retained legacy correlation policy uses dotted branch
    identifiers such as ``1``, ``2.1``, and ``2.2`` instead.
    """

    weights: dict[str, float]
    cluster_paths: dict[str, str]


class PortfolioWeightPolicy(ABC):
    """Map a trial book and its correlation block to auditable risk weights."""

    @abstractmethod
    def allocate(
        self, symbols: list[str], correlation: np.ndarray
    ) -> PortfolioWeightAllocation:
        """Return normalized weights and a cluster path for every symbol."""

    def weights(self, symbols: list[str], correlation: np.ndarray) -> dict[str, float]:
        """Return normalized weights while preserving the former compact API."""
        return self.allocate(symbols, correlation).weights


class EqualPortfolioWeights(PortfolioWeightPolicy):
    """Assign the same risk weight to every instrument in a trial book."""

    def allocate(
        self, symbols: list[str], correlation: np.ndarray
    ) -> PortfolioWeightAllocation:
        """Return ``1/n`` weights in one common group.

        ``correlation`` is intentionally unused because this policy is the
        transparent equal-weight sensitivity rather than a clustering policy.
        """
        del correlation
        weights = (
            {symbol: 1.0 / len(symbols) for symbol in symbols} if symbols else {}
        )
        return PortfolioWeightAllocation(
            weights=weights,
            cluster_paths={symbol: "1" for symbol in symbols},
        )


class TopDownClassificationWeights(PortfolioWeightPolicy):
    """Allocate equally down the reviewed economic classification tree.

    Every represented asset class receives equal risk. Within an asset class,
    every represented economic subclass receives equal risk, and instruments
    then share their subclass allocation equally. Correlations remain relevant
    to the trial's final IDM and score, but they no longer invent opaque groups
    such as an FX cross and gasoil occupying the same allocation branch.

    Parameters
    ----------
    classification_by_symbol
        Mapping from executable symbol to canonical lowercase
        ``(asset_class, economic_subclass)`` identifiers.
    """

    def __init__(
        self,
        classification_by_symbol: dict[str, tuple[str, str]],
    ) -> None:
        """Store the symbol hierarchy used for every hypothetical book."""
        self.classification_by_symbol = classification_by_symbol

    def allocate(
        self, symbols: list[str], correlation: np.ndarray
    ) -> PortfolioWeightAllocation:
        """Return class/subclass/instrument equal-risk weights.

        ``correlation`` is intentionally unused here. It is applied after this
        structural allocation when the selector calculates IDM and portfolio
        score.
        """
        del correlation
        if not symbols:
            return PortfolioWeightAllocation({}, {})

        missing = [
            symbol
            for symbol in symbols
            if symbol not in self.classification_by_symbol
            or not all(self.classification_by_symbol[symbol])
        ]
        if missing:
            raise ValueError(
                "top-down weighting requires asset class and economic "
                f"subclass for: {missing}"
            )

        members: dict[str, dict[str, list[str]]] = {}
        for symbol in symbols:
            asset_class, subclass = self.classification_by_symbol[symbol]
            members.setdefault(asset_class, {}).setdefault(subclass, []).append(
                symbol
            )

        class_weight = 1.0 / len(members)
        weights: dict[str, float] = {}
        paths: dict[str, str] = {}
        for asset_class, subclasses in members.items():
            subclass_weight = class_weight / len(subclasses)
            for subclass, subclass_symbols in subclasses.items():
                instrument_weight = subclass_weight / len(subclass_symbols)
                for symbol in subclass_symbols:
                    weights[symbol] = instrument_weight
                    paths[symbol] = f"{asset_class}/{subclass}"
        return PortfolioWeightAllocation(weights, paths)


class HandcraftedPortfolioWeights(PortfolioWeightPolicy):
    """Implement the legacy correlation-derived recursive risk weighting.

    Instruments are split into two correlation clusters. Each cluster receives
    half the parent allocation after adjustment for its internal IDM, and
    clusters larger than two instruments are split recursively. Final raw
    weights are normalized to one.
    """

    def allocate(
        self, symbols: list[str], correlation: np.ndarray
    ) -> PortfolioWeightAllocation:
        """Return normalized handcrafted weights and recursive cluster paths."""
        if not symbols:
            return PortfolioWeightAllocation({}, {})
        raw, cluster_paths = self._recursive(symbols, correlation)
        total = sum(raw.values())
        weights = {symbol: raw[symbol] / total for symbol in symbols}
        # A one- or two-instrument book reaches the base case before a split;
        # label it as one terminal cluster so every persisted row has a path.
        normalized_paths = {
            symbol: cluster_paths[symbol] or "1" for symbol in symbols
        }
        return PortfolioWeightAllocation(weights, normalized_paths)

    def _recursive(
        self, symbols: list[str], correlation: np.ndarray
    ) -> tuple[dict[str, float], dict[str, str]]:
        """Return raw weights and relative cluster paths for one tree node."""
        if len(symbols) <= 2:
            return (
                {symbol: 1.0 / len(symbols) for symbol in symbols},
                {symbol: "" for symbol in symbols},
            )
        try:
            link = sch.linkage(distance.pdist(correlation), method="complete")
            cutoff = link[len(symbols) - 2][2] - 1e-6
            labels = sch.fcluster(link, cutoff, criterion="distance")
            if len(set(labels)) != 2:
                raise ValueError("clustering did not produce two groups")
        except (ValueError, FloatingPointError):
            labels = np.array([(idx % 2) + 1 for idx in range(len(symbols))])

        result: dict[str, float] = {}
        cluster_paths: dict[str, str] = {}
        for branch, label in enumerate(sorted(set(labels)), start=1):
            indices = [idx for idx, value in enumerate(labels) if value == label]
            group_symbols = [symbols[idx] for idx in indices]
            group_correlation = correlation[np.ix_(indices, indices)]
            group_weights, child_paths = self._recursive(
                group_symbols, group_correlation
            )
            group_idm = _handcrafted_diversification_multiplier(
                group_symbols,
                group_correlation,
                group_weights,
            )
            for symbol, weight in group_weights.items():
                result[symbol] = 0.5 * group_idm * weight
                child_path = child_paths[symbol]
                cluster_paths[symbol] = (
                    f"{branch}.{child_path}" if child_path else str(branch)
                )
        return result, cluster_paths


@dataclass(frozen=True)
class PortfolioScore:
    """Describe one complete trial book and all instrument-level diagnostics.

    ``score`` is negative infinity when any member violates a hard size or
    liquidity constraint. ``details`` retains the calculated position, cost,
    penalty, risk traded, and participation for every member.
    """

    symbols: tuple[str, ...]
    weights: dict[str, float]
    cluster_paths: dict[str, str]
    idm: float
    score: float
    feasible: bool
    reason: str
    details: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class GreedySelectionResult:
    """Return the accepted book plus summary and member-level trial audits."""

    selected: tuple[str, ...]
    final_score: PortfolioScore
    trials: tuple[dict[str, object], ...]
    trial_allocations: tuple[dict[str, object], ...]


class GreedyInstrumentSelector:
    """Add the best feasible candidate until score falls below tolerance.

    The selector first scores every singleton with IDM=1, subject to
    ``max_weight_idm``. It then tries every unused economic family as the next
    addition, fully rescoring the hypothetical book. Every trial retains the
    same cap on ``weight * IDM`` for sizing constraints. The first
    two-instrument book sets the score benchmark; later winners must remain
    above ``score_threshold * best_seen``.
    """

    def __init__(
        self,
        *,
        config: GreedySelectionConfig | None = None,
        weight_policy: PortfolioWeightPolicy | None = None,
    ) -> None:
        """Initialize the selector with configurable scoring and weight policies."""
        self.config = config or GreedySelectionConfig()
        # ``None`` selects the reviewed economic hierarchy dynamically from
        # each candidate set. Explicit policies remain available for research
        # comparisons against equal or legacy correlation-derived weighting.
        self.weight_policy = weight_policy

    def _weight_policy_for(
        self, candidates: dict[str, SelectionCandidate]
    ) -> PortfolioWeightPolicy:
        """Return the configured policy or build the default hierarchy policy."""
        if self.weight_policy is not None:
            return self.weight_policy
        return TopDownClassificationWeights({
            symbol: (candidate.asset_class, candidate.economic_subclass)
            for symbol, candidate in candidates.items()
        })

    @staticmethod
    def _rounded_max_contracts(max_position: float) -> int:
        """Convert a positive continuous cap position to an executable lot count.

        Futures allocation elsewhere in the engine uses deterministic half-up
        rounding because Python's ``round`` sends exact half lots to the nearest
        even integer. Keeping selection on the same rule makes ``0.5`` one
        contract, ``1.5`` two contracts, and so on.

        Parameters
        ----------
        max_position
            Positive continuous contract position at the forecast cap.

        Returns
        -------
        int
            Non-negative whole-contract estimate.
        """
        if not math.isfinite(max_position) or max_position < 0:
            raise ValueError("max_position must be finite and non-negative")
        return 0 if max_position < 0.5 else math.floor(max_position + 0.5)

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
        allocation_result = self._weight_policy_for(candidates).allocate(
            symbols, correlation
        )
        weights = allocation_result.weights
        if starting and len(symbols) == 1:
            idm = 1.0
        else:
            idm = _handcrafted_diversification_multiplier(
                symbols,
                correlation,
                weights,
                maximum=self.config.max_idm,
            )
        # Carver caps instrument_weight * IDM inside maximum-position sizing.
        # Express the capped product as an equivalent pre-IDM weight so the
        # existing position and liquidity equations retain one clear form.
        weights_for_limits = {
            symbol: min(
                weights[symbol],
                self.config.max_weight_idm / idm,
            )
            for symbol in symbols
        }

        details: list[dict[str, object]] = []
        feasible = True
        reasons: list[str] = []
        net_sharpes: dict[str, float] = {}
        instrument_margin_limit_usd = (
            self.config.capital
            * self.config.max_instrument_margin_pct
            / 100.0
            if self.config.max_instrument_margin_pct is not None else None
        )
        # Enabling the per-instrument gate also activates a cumulative book
        # budget equal to starting capital unless the caller asks for less.
        effective_book_margin_pct = self.config.max_book_margin_pct
        if (
            effective_book_margin_pct is None
            and self.config.max_instrument_margin_pct is not None
        ):
            effective_book_margin_pct = 100.0
        book_margin_limit_usd = (
            self.config.capital * effective_book_margin_pct / 100.0
            if effective_book_margin_pct is not None else None
        )
        if self.config.max_instruments_per_cluster is not None:
            cluster_counts: dict[str, int] = {}
            for symbol in symbols:
                cluster = candidates[symbol].economic_subclass
                cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
            for cluster, count in sorted(cluster_counts.items()):
                if count > self.config.max_instruments_per_cluster:
                    feasible = False
                    reasons.append(
                        f"cluster_instr_limit:{cluster} n={count} "
                        f"max={self.config.max_instruments_per_cluster}"
                    )
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
            # The AFTS baseline subtracts combined-forecast trading cost. The
            # optional conservative mode also deducts scheduled holding rolls.
            score_cost_sr = (
                candidate.annual_cost_sr
                if self.config.include_roll_cost_in_score
                else candidate.annual_trade_cost_sr
            )
            net_sr = self.config.gross_sr - score_cost_sr - size_penalty
            # The forecast-cap position is twice the average position under
            # the conventional 20/10 forecast ratio. Translate both turnover
            # and scheduled rolls into actual annual contract volume and USD
            # cost without changing the trade-only AFTS ranking objective.
            avg_position = max_position / self.config.forecast_cap_ratio
            ann_trade_cons = avg_position * candidate.combined_turnover
            ann_roll_cons = avg_position * candidate.ann_rolls
            ann_trade_cost_usd = (
                ann_trade_cons * candidate.trade_cost_usd_per_con
            )
            ann_roll_cost_usd = ann_roll_cons * candidate.roll_cost_usd_per_con
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
            max_contracts = self._rounded_max_contracts(max_position)
            continuous_margin_at_max_usd = (
                max_position * candidate.initial_margin_usd
                if candidate.initial_margin_usd is not None
                else None
            )
            # Margin is occupied in whole lots. Retain the continuous value so
            # the extra heuristic can be compared with Carver's approximation.
            margin_at_max_usd = (
                max_contracts * candidate.initial_margin_usd
                if candidate.initial_margin_usd is not None
                else None
            )
            reason = ""
            if not math.isfinite(size_penalty):
                reason = "max_position_below_half_contract"
            elif pct_market is None:
                reason = "market_risk_volume_unavailable"
            elif pct_market > self.config.max_pct_market_volume:
                reason = "market_participation_limit"
            elif (
                (
                    instrument_margin_limit_usd is not None
                    or book_margin_limit_usd is not None
                )
                and margin_at_max_usd is None
            ):
                reason = "initial_margin_unavailable"
            elif (
                instrument_margin_limit_usd is not None
                and margin_at_max_usd is not None
                and margin_at_max_usd > instrument_margin_limit_usd
            ):
                reason = "instrument_margin_limit"
            if reason:
                feasible = False
                reasons.append(f"{symbol}:{reason}")
            net_sharpes[symbol] = net_sr
            details.append({
                "symbol": symbol,
                "asset_cls": candidate.asset_class,
                "econ_subcls": candidate.economic_subclass,
                "econ_fam_id": candidate.econ_family,
                "cluster_path": allocation_result.cluster_paths[symbol],
                "weight": weights[symbol],
                "limit_weight": allocation,
                "roll_cost_in_score": self.config.include_roll_cost_in_score,
                "score_cost_sr": score_cost_sr,
                "ann_trade_cost_sr": candidate.annual_trade_cost_sr,
                "ann_roll_cost_sr": candidate.annual_roll_cost_sr,
                "ann_cost_sr": candidate.annual_cost_sr,
                "avg_cons": avg_position,
                "ann_trade_cons": ann_trade_cons,
                "ann_roll_cons": ann_roll_cons,
                "trade_cost_usd_per_con": candidate.trade_cost_usd_per_con,
                "roll_cost_usd_per_con": candidate.roll_cost_usd_per_con,
                "ann_trade_cost_usd": ann_trade_cost_usd,
                "ann_roll_cost_usd": ann_roll_cost_usd,
                "ann_cost_usd": ann_trade_cost_usd + ann_roll_cost_usd,
                "max_position": max_position,
                "max_cons": max_contracts,
                "size_penalty_sr": size_penalty,
                "net_sr": net_sr,
                "risk_traded_day": risk_traded_day,
                "pct_mkt_volume": pct_market,
                "init_margin_usd": candidate.initial_margin_usd,
                "cont_margin_at_max_usd": continuous_margin_at_max_usd,
                "margin_at_max_usd": margin_at_max_usd,
                "instr_margin_limit_usd": instrument_margin_limit_usd,
                "book_margin_limit_usd": book_margin_limit_usd,
                "reason": reason,
            })

        total_continuous_margin_at_max_usd = sum(
            float(detail["cont_margin_at_max_usd"])
            for detail in details
            if detail["cont_margin_at_max_usd"] is not None
        )
        total_margin_at_max_usd = sum(
            float(detail["margin_at_max_usd"])
            for detail in details
            if detail["margin_at_max_usd"] is not None
        )
        if (
            book_margin_limit_usd is not None
            and all(detail["margin_at_max_usd"] is not None for detail in details)
            and total_margin_at_max_usd > book_margin_limit_usd
        ):
            feasible = False
            reasons.append(
                "portfolio_margin_limit "
                f"margin_usd={total_margin_at_max_usd:.2f} "
                f"max_usd={book_margin_limit_usd:.2f}"
            )
        for detail in details:
            detail["portfolio_cont_margin_at_max_usd"] = (
                total_continuous_margin_at_max_usd
            )
            detail["portfolio_margin_at_max_usd"] = total_margin_at_max_usd
            detail["book_margin_remaining_usd"] = (
                book_margin_limit_usd - total_margin_at_max_usd
                if book_margin_limit_usd is not None else None
            )

        # Portfolio score rewards diversification through the same w'Hw risk
        # denominator used by IDM, but applies each member's net SR in the
        # numerator before deciding whether the candidate improves the book.
        vector = np.array([weights[symbol] for symbol in symbols])
        denominator = math.sqrt(max(float(vector @ correlation @ vector), 0.0))
        numerator = sum(weights[symbol] * net_sharpes[symbol] for symbol in symbols)
        score = numerator / denominator if denominator > 0 and feasible else -math.inf
        return PortfolioScore(
            symbols=tuple(symbols),
            weights=weights,
            cluster_paths=allocation_result.cluster_paths,
            idm=idm,
            score=score,
            feasible=feasible,
            reason=";".join(reasons),
            details=tuple(details),
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
        trial_allocations: list[dict[str, object]] = []

        # Seed selection uses a conservative nominal portfolio weight rather
        # than pretending a one-market account permanently holds 100% weight.
        singleton_scores = []
        for symbol in full_symbols:
            scored = self.score(
                [symbol], candidates, full_symbols, correlation, starting=True
            )
            singleton_scores.append(scored)
            self._record_trial(
                trials, trial_allocations, 0, symbol, scored, accepted=False
            )
        feasible_singletons = [score for score in singleton_scores if score.feasible]
        if not feasible_singletons:
            raise ValueError("no candidate passed singleton granularity and liquidity")
        current = max(feasible_singletons, key=lambda item: (item.score, item.symbols[0]))
        selected = list(current.symbols)
        # Match the published AFTS loop: singleton scoring chooses the seed,
        # while the first n+1 book establishes the tolerance benchmark.
        best_seen = 0.0
        self._finalize_iteration(
            trials,
            trial_allocations,
            iteration=0,
            best_candidate=selected[-1],
            best_score=current.score,
            min_accept_score=None,
            accepted=True,
        )

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
                self._record_trial(
                    trials,
                    trial_allocations,
                    iteration,
                    symbol,
                    scored,
                    accepted=False,
                )
            feasible = [item for item in scored_trials if item[1].feasible]
            if not feasible:
                break
            winner, winning_score = max(
                feasible, key=lambda item: (item[1].score, item[0])
            )
            min_accept_score = self.config.score_threshold * best_seen
            if winning_score.score < min_accept_score:
                self._finalize_iteration(
                    trials,
                    trial_allocations,
                    iteration=iteration,
                    best_candidate=winner,
                    best_score=winning_score.score,
                    min_accept_score=min_accept_score,
                    accepted=False,
                )
                break
            selected.append(winner)
            current = winning_score
            best_seen = max(best_seen, winning_score.score)
            self._finalize_iteration(
                trials,
                trial_allocations,
                iteration=iteration,
                best_candidate=winner,
                best_score=winning_score.score,
                min_accept_score=min_accept_score,
                accepted=True,
            )
            iteration += 1

        return GreedySelectionResult(
            tuple(selected), current, tuple(trials), tuple(trial_allocations)
        )

    @classmethod
    def _record_trial(
        cls,
        trials: list[dict[str, object]],
        trial_allocations: list[dict[str, object]],
        iteration: int,
        candidate: str,
        score: PortfolioScore,
        *,
        accepted: bool,
    ) -> None:
        """Persist summary and per-book-member state for one candidate trial."""
        trials.append(cls._trial_row(iteration, candidate, score, accepted=accepted))
        trial_allocations.extend(
            cls._trial_allocation_rows(
                iteration, candidate, score, accepted=accepted
            )
        )

    @staticmethod
    def _trial_row(
        iteration: int,
        candidate: str,
        score: PortfolioScore,
        *,
        accepted: bool,
    ) -> dict[str, object]:
        """Flatten a portfolio score into one persisted candidate-trial row."""
        first_detail = score.details[0] if score.details else {}
        return {
            "iteration": iteration,
            "candidate": candidate,
            "book": ",".join(score.symbols),
            "score": score.score if math.isfinite(score.score) else None,
            "idm": score.idm,
            "feasible": score.feasible,
            "accepted": accepted,
            "reason": score.reason,
            "roll_cost_in_score": first_detail.get("roll_cost_in_score"),
            "portfolio_cont_margin_at_max_usd": first_detail.get(
                "portfolio_cont_margin_at_max_usd"
            ),
            "portfolio_margin_at_max_usd": first_detail.get(
                "portfolio_margin_at_max_usd"
            ),
            "book_margin_remaining_usd": first_detail.get(
                "book_margin_remaining_usd"
            ),
            "instr_margin_limit_usd": first_detail.get(
                "instr_margin_limit_usd"
            ),
            "book_margin_limit_usd": first_detail.get(
                "book_margin_limit_usd"
            ),
            "iter_best": None,
            "iter_best_score": None,
            "min_accept_score": None,
        }

    @staticmethod
    def _trial_allocation_rows(
        iteration: int,
        candidate: str,
        score: PortfolioScore,
        *,
        accepted: bool,
    ) -> list[dict[str, object]]:
        """Expand one hypothetical book into auditable member allocation rows."""
        rows: list[dict[str, object]] = []
        book = ",".join(score.symbols)
        # Make each terminal cluster readable without requiring the consumer
        # to group dotted paths across multiple CSV rows.
        members_by_cluster: dict[str, list[str]] = {}
        weight_by_cluster: dict[str, float] = {}
        for symbol in score.symbols:
            cluster_path = score.cluster_paths[symbol]
            members_by_cluster.setdefault(cluster_path, []).append(symbol)
            weight_by_cluster[cluster_path] = (
                weight_by_cluster.get(cluster_path, 0.0) + score.weights[symbol]
            )
        for detail in score.details:
            cluster_path = str(detail["cluster_path"])
            rows.append({
                "iteration": iteration,
                "candidate": candidate,
                "accepted": accepted,
                "book": book,
                "book_symbol": detail["symbol"],
                "asset_cls": detail["asset_cls"],
                "econ_subcls": detail["econ_subcls"],
                "econ_fam_id": detail["econ_fam_id"],
                "cluster_path": cluster_path,
                "cluster_members": ",".join(members_by_cluster[cluster_path]),
                "cluster_weight": weight_by_cluster[cluster_path],
                "weight": detail["weight"],
                "limit_weight": detail["limit_weight"],
                "idm": score.idm,
                "score": score.score if math.isfinite(score.score) else None,
                "roll_cost_in_score": detail["roll_cost_in_score"],
                "score_cost_sr": detail["score_cost_sr"],
                "ann_trade_cost_sr": detail["ann_trade_cost_sr"],
                "ann_roll_cost_sr": detail["ann_roll_cost_sr"],
                "ann_cost_sr": detail["ann_cost_sr"],
                "size_penalty_sr": detail["size_penalty_sr"],
                "avg_cons": detail["avg_cons"],
                "ann_trade_cons": detail["ann_trade_cons"],
                "ann_roll_cons": detail["ann_roll_cons"],
                "trade_cost_usd_per_con": detail["trade_cost_usd_per_con"],
                "roll_cost_usd_per_con": detail["roll_cost_usd_per_con"],
                "ann_trade_cost_usd": detail["ann_trade_cost_usd"],
                "ann_roll_cost_usd": detail["ann_roll_cost_usd"],
                "ann_cost_usd": detail["ann_cost_usd"],
                "feasible": score.feasible,
                "reason": score.reason,
                "instr_reason": detail["reason"],
                "max_position": detail["max_position"],
                "max_cons": detail["max_cons"],
                "init_margin_usd": detail["init_margin_usd"],
                "cont_margin_at_max_usd": detail[
                    "cont_margin_at_max_usd"
                ],
                "margin_at_max_usd": detail["margin_at_max_usd"],
                "portfolio_cont_margin_at_max_usd": detail[
                    "portfolio_cont_margin_at_max_usd"
                ],
                "portfolio_margin_at_max_usd": detail[
                    "portfolio_margin_at_max_usd"
                ],
                "book_margin_remaining_usd": detail[
                    "book_margin_remaining_usd"
                ],
                "instr_margin_limit_usd": detail["instr_margin_limit_usd"],
                "book_margin_limit_usd": detail["book_margin_limit_usd"],
                "iter_best": None,
                "iter_best_score": None,
                "min_accept_score": None,
            })
        return rows

    @classmethod
    def _finalize_iteration(
        cls,
        trials: list[dict[str, object]],
        trial_allocations: list[dict[str, object]],
        *,
        iteration: int,
        best_candidate: str,
        best_score: float,
        min_accept_score: float | None,
        accepted: bool,
    ) -> None:
        """Attach the competitive decision to every row in one search pass.

        Feasibility reasons are calculated while scoring a hypothetical book.
        This second step explains why each *feasible* book was or was not
        chosen relative to the other books attempted in the same iteration.
        Member-level allocation rows receive the identical decision metadata
        so they remain interpretable without joining back to the trial CSV.
        """
        for rows in (trials, trial_allocations):
            cls._finalize_iteration_rows(
                rows,
                iteration=iteration,
                best_candidate=best_candidate,
                best_score=best_score,
                min_accept_score=min_accept_score,
                accepted=accepted,
            )

    @staticmethod
    def _finalize_iteration_rows(
        rows: list[dict[str, object]],
        *,
        iteration: int,
        best_candidate: str,
        best_score: float,
        min_accept_score: float | None,
        accepted: bool,
    ) -> None:
        """Finalize winner metadata and rejection reasons for one row set."""
        for row in rows:
            if row["iteration"] != iteration:
                continue
            row["iter_best"] = best_candidate
            row["iter_best_score"] = best_score
            row["min_accept_score"] = min_accept_score
            candidate = str(row["candidate"])
            if candidate == best_candidate:
                row["accepted"] = accepted
                if not accepted and not row["reason"]:
                    row["reason"] = "best_score_below_acceptance_threshold"
            elif row["feasible"] and not row["reason"]:
                # A feasible loser needs a competitive explanation; the hard
                # constraint path above already populated infeasible reasons.
                if iteration == 0:
                    row["reason"] = "lower_rank_than_selected_seed"
                elif accepted:
                    row["reason"] = "lower_rank_than_iteration_winner"
                else:
                    row["reason"] = "lower_rank_than_iteration_best"
