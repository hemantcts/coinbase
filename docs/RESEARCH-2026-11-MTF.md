# Research note: multi-timeframe entries (5–6 Nov 2026)

*BTC-EUR + ETH-EUR, 5 Nov 2024 – 5 Nov 2026, `client` profile, CHF 1 000 equivalent, maker 0.6 % /
taker 1.2 %, slippage 0.1 %. Figures in EUR after costs.*

## Question

Does timing trendhold's entries with lower timeframes help? `mtf` buys only when the 1 day and
6 h trend agree, 1 h and 30 min show momentum and a 15 min candle closes back above its EMA 20.
Exits and the 6x ATR trailing stop are trendhold's, on 6 h.

## Results

| | net | buy & hold | worst vs start | max DD | trades | win | fees |
|---|---|---|---|---|---|---|---|
| **trendhold (6 h, current)** | **+2.1 %** | +1.2 % | −1.6 % | 9.4 % | 37 | 24 % | 57 |
| mtf (15 min entries) | +0.2 % | +1.4 % | −2.1 % | 8.2 % | 33 | 30 % | 53 |

Walk-forward (first 60 % in-sample, last 40 % out-of-sample):

| | in-sample | out-of-sample | market out-of-sample |
|---|---|---|---|
| trendhold | +4.5 % | −1.0 % | −5.7 % |
| mtf | +2.5 % | −2.4 % | −5.6 % |

## What this means

- The lower timeframes did what they were built for: slightly fewer trades (33 vs 37), a higher win rate
  (30 % vs 24 %) and a smaller drawdown (8.2 % vs 9.4 %). The fee load stayed the same.
- They did not improve the result. Waiting for the 1 h / 30 min / 15 min confirmation entered later into
  trends that were already moving and skipped some of the best early entries. Net +0.2 % against +2.1 %,
  and weaker in both walk-forward halves.
- 15-minute data adds no edge to a 6-hour trend signal here, consistent with the October research.
  Recommendation: keep `trendhold` on 6 h. `mtf` stays in the code, off by default, for re-testing when
  the fee tier drops or more history is available.

## Update 6 Nov: seven timeframes (1m, 15m, 30m, 1h, 2h, 4h, 1d)

`mtf` now reads 1 day + 4 h for direction, 2 h + 1 h + 30 min for momentum, 15 min for the trigger and
1 min as an execution check (no sharp drop in the last 5 minutes). Exits and the trailing stop moved
from 6 h to 4 h. 2 h and 4 h are resampled from 1 h. The backtest has no two years of 1-minute history,
so the 1-minute check is skipped there (it runs live).

| 6 Nov 2024 – 6 Nov 2026 | net | buy & hold | worst vs start | max DD | trades | win | fees |
|---|---|---|---|---|---|---|---|
| **trendhold (6 h, current)** | **+2.1 %** | −3.2 % | −1.6 % | 9.4 % | 37 | 24 % | 57 |
| mtf, 7 timeframes | +1.2 % | −1.1 % | **−0.8 %** | **7.5 %** | 38 | 29 % | 61 |
| mtf, first version (6 h exits) | +0.2 % | | −2.1 % | 8.2 % | 33 | 30 % | 53 |

Walk-forward: mtf +2.8 % in-sample / −1.1 % out-of-sample (market −5.6 %); trendhold +4.6 % / −1.0 %.

The seven-timeframe version is clearly better than the first one and has the smallest losses of all
three (worst point −0.8 %, drawdown 7.5 %), but it still earns less than trendhold and is equal out of
sample. It is a lower-risk alternative, not a better one. Recommendation unchanged: trendhold stays
configured; mtf is a candidate for a side-by-side paper run.

## Reproduce

```bash
export DB_PATH=data/research.sqlite3
python -m trader fetch-history --granularity 900 --days 730 --products BTC-EUR ETH-EUR
python -m trader fetch-history --granularity 86400 --days 900 --products BTC-EUR ETH-EUR
python -m trader fetch-history --granularity 3600 --days 730 --products BTC-EUR ETH-EUR   # 2h / 4h are built from 1h
python -m trader backtest --granularity 900 --days 730 --strategy mtf --profile client --products BTC-EUR ETH-EUR --walk-forward
python -m trader backtest --granularity 21600 --days 730 --strategy trendhold --profile client --products BTC-EUR ETH-EUR --walk-forward
```
