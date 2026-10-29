# 5PPW Credit Spread Strategy Analysis

**Source:** `~/fin/5PPW Results Sheet - Results.csv`  
**Period:** Feb 7, 2023 – Jul 2, 2026 (174 trades, ~3.4 years)  
**Strategy:** Weekly 5-point wide SPX/SPXW vertical credit spreads, ~1 trade/week

---

## Profitability Summary

| Metric | Value |
|---|---|
| Total P&L | +$1,818 |
| ROI on $500 capital | 363.6% |
| Annualized (simple) | 73.2% |
| After ~$350 brokerage fees (est.) | ~+$1,470 (294%) |
| Win rate | 89.1% (155W / 19L) |
| Avg win | +$23.70 |
| Avg loss | -$97.63 |
| Profit factor | 1.98× |
| Expected value/trade | ~$10.45 |
| Max drawdown | $545 (peak $2,243 on 3/26/2026) |

**By year:**

| Year | Trades | Win Rate | P&L |
|---|---|---|---|
| 2023 | 57 | 88% | +$403 |
| 2024 | 49 | 88% | +$655 |
| 2025 | 48 | 92% | +$910 |
| 2026 | 20 | 90% | -$150 |

---

## Structure

- **Instrument:** SPX / SPXW (S&P 500 index options, cash-settled)
- **Spread width:** Always 5 points ($500 max loss per contract)
- **Credit collected:** $0.25/share historically ($25 net), reduced to $0.10 ($10 net) in 2026
- **Max risk:** $475–$490 (spread minus credit)
- **DTE:** ~4–7 DTE (weekly options, entered Mon/Tue, expiring Fri)
- **Holding period:** Multi-session — held overnight until expiry or stop triggered
- **Stop-loss rule:** Exit when spread reaches $1.00 (4× the credit on $0.25 trades)
- **Bias:** 61% call spreads / 39% put spreads

---

## Call vs Put Breakdown

| Type | Count | Win Rate |
|---|---|---|
| Call spreads | 107 (61%) | 91.6% |
| Put spreads | 67 (39%) | 85.1% |

---

## Loss Analysis

17 of 19 losses were orderly stops (~$72 avg), exiting when the spread reached $1.00.

Two 2026 trades broke stop discipline:

| Date | Credit | Exit | Loss | % of Max |
|---|---|---|---|---|
| 3/31/2026 | $0.10 | $4.10 | -$400 | 81.6% |
| 5/12/2026 | $0.10 | $2.40 | -$230 | 46.9% |

These two trades alone cost -$630, exceeding all 2026 winning trades combined.

---

## Key Issues

1. **Stop discipline failed in 2026.** The $0.10-credit trades require a stop at $0.40 (4× rule), but both losses ran well past that. Likely held expecting recovery against a fast market move.

2. **Credit compression worsens risk/reward.** Moving from $0.25 to $0.10 credits shifts the max-loss-to-max-gain ratio from 20:1 to 50:1 without a corresponding adjustment to position sizing or stops.

3. **Results are unaudited.** The provider's own disclaimer states results have not been independently verified.

4. **Fees excluded.** ~$2/trade × 174 trades ≈ $348 not counted, reducing actual ROI by ~19% in absolute dollar terms.

5. **Current drawdown.** As of 7/2/2026, equity is $1,818 — still $425 below the 3/26/2026 all-time high of $2,243.

---

## Scaling to $15,000/Trade (30 Contracts)

$15,000 ÷ $500 max risk per contract = 30 contracts per weekly trade.

### Fees by Broker

| Broker | Open | Close (win) | Close (loss) |
|---|---|---|---|
| **Tastytrade** | $20 | $0 | $20 |
| IBKR Pro | $50 | $0 | $50 |
| Schwab/ToS | $39 | $0 | $39 |

Tastytrade is most cost-effective at this scale — SPX is a broad-based index option so they cap fees at **$10/leg regardless of contract count**. 30 contracts costs the same $20 to open as 2 contracts.

### Slippage

Far-OTM 5-point SPX spreads are thin at $0.10–$0.25 credit. Filling 30 contracts requires working the order for 1–2 minutes; expect $0.02–$0.04 slippage on the spread:

| Credit | Gross Win (30 cts) | Slippage Est. | Drag |
|---|---|---|---|
| $0.25 (historical) | $750 | $60–$120 | 8–16% |
| $0.10 (2026) | $300 | $60–$120 | **20–40%** |

At $0.10 credit, slippage alone can eat a third of the winning trade.

### Net P&L Impact (174-trade history, scaled)

| | Tastytrade | IBKR Pro |
|---|---|---|
| Gross | $54,540 | $54,540 |
| Fees | -$3,859 | -$9,648 |
| Slippage (~$40/trade) | -$6,960 | -$6,960 |
| **Net** | **$43,721 (80%)** | **$37,932 (70%)** |

### Risk at Scale

| Scenario | 1 contract | 30 contracts |
|---|---|---|
| Normal stop loss | -$75 | -$2,250 |
| May 2026 loss | -$230 | -$6,900 |
| March 2026 loss | -$400 | **-$12,000** |
| Max possible loss | -$490 | **-$14,700** |

At $0.10 credit: collect $300, risk $14,700 — **50:1 max-loss-to-max-gain**. A single undisciplined loss like March 2026 wipes roughly 3 years of winning trades at this scale.

---

## Verdict

The strategy has real positive expected value (~$10.45/trade) and the underlying structure — far-OTM weekly SPX credit spreads with a defined 4× stop — is sound. The 3.4-year track record is internally consistent. However:

- The edge per trade is small and easily eroded by fees or a single undisciplined loss
- 2026 demonstrates the strategy's tail risk: two gap-through losses nearly offset three years of prior gains
- The shift to $0.10 credits has weakened the risk/reward significantly
- Not suitable as a scalable strategy at meaningful account sizes (SPX spreads have liquidity/slippage constraints at size)
