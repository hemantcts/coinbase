# Research note: top-10 coins, adaptive strategy switching and DCA (5 Nov 2026)

*Two years of 6-hour candles, 5 Nov 2024 – 5 Nov 2026, CHF 1 000 equivalent, maker 0.6 % / taker 1.2 %,
slippage 0.1 %, total-loss pause 30 days. All figures in EUR after costs.*

## What was built

- `trader top-coins` / `PRODUCTS=auto`: ranks Coinbase EUR markets by 30-day volume (50 %), daily ATR in a
  3–8 % sweet spot (30 %) and trend (20 %); stablecoins, delisted and restricted markets excluded.
  On 5 Nov 2026: BTC, ETH, SOL, XRP, BAT, BCH, ADA, ALGO, LINK, DOT.
- `STRATEGY=adaptive`: per coin and per candle, classifies the market (uptrend, range, downtrend, mixed) and
  picks a play: trend entry, swing pullback, DCA ladder, or cash. Scalping was left out: the October research
  showed fast strategies lose to Coinbase's fees.
- `RISK_PROFILE=client-dca`: the client's limits unchanged, plus a DCA ladder that the risk controller
  enforces (4 rungs of 2.5 % = 10 % per coin, 5 % apart, hard stop 18 % below the first rung).

## Results

| configuration | net | buy & hold | worst vs start | trades | fees | total-loss halt |
|---|---|---|---|---|---|---|
| **trendhold, BTC+ETH (current)** | **+2.1 %** | +1.2 % | −1.6 % | 37 | 57 | no |
| adaptive + DCA, BTC+ETH | +2.6 % | +1.5 % | −2.6 % | 51 | 52 | no |
| trendhold, top 10 | −15.5 % | −1.4 % | −24.0 % | 76 | 84 | no |
| adaptive + DCA, top 10 | −15.1 % | −1.4 % | −25.5 % | 142 | 101 | yes |

Walk-forward, adaptive + DCA on the top 10: in-sample (438 days) −8.1 % vs market +12.2 %;
out-of-sample (292 days) −1.6 % vs market −9.8 %.

Where the top-10 loss came from (adaptive): by play, trend −103, swing −19, DCA −29; by coin,
ALGO −57, ADA −57, DOT −39, BTC −22, LINK −15, BCH −14, SOL −10, XRP +6, BAT +24, ETH +33.

## What this means

1. **More coins made it worse, not better.** Altcoins trend less cleanly and whipsaw the trend entries;
   they also fill the 30 % exposure cap, which crowds out BTC/ETH trades. The top-10 list was chosen with
   today's data, which flatters the backtest, and it still lost.
2. **Adaptive switching is marginally better than trendhold on BTC+ETH** (+2.6 % vs +2.1 %), with a
   slightly deeper worst point. The difference is within noise; it is not a reason to switch on its own.
3. **DCA roughly broke even** (−0.9 on BTC+ETH, −29 on the top 10). It does not add an edge here; the
   ladder and hard stop did keep each coin's loss bounded as designed.
4. The current configuration (trendhold, BTC+ETH, `client`) remains the only tested one that is positive with
   a small worst case. Recommendation: keep it for the paper run and the 15 Nov review. If the client wants
   more coins, run `adaptive` with `client-dca` on a **separate paper database** next to it, limited to the
   few coins that held up (BTC, ETH, possibly XRP), and compare after 30 days.

## Reproduce

```bash
export DB_PATH=data/research.sqlite3
python -m trader import-history
python -m trader top-coins
python -m trader fetch-history --granularity 21600 --days 730 --products SOL-EUR XRP-EUR BAT-EUR BCH-EUR ADA-EUR ALGO-EUR LINK-EUR DOT-EUR
python -m trader backtest --granularity 21600 --days 730 --strategy adaptive --profile client-dca \
  --products BTC-EUR ETH-EUR SOL-EUR XRP-EUR BAT-EUR BCH-EUR ADA-EUR ALGO-EUR LINK-EUR DOT-EUR --walk-forward
```
