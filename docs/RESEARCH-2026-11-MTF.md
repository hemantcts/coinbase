# Research note: multi-timeframe entries (5 Nov 2026)

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

## Reproduce

```bash
export DB_PATH=data/research.sqlite3
python -m trader fetch-history --granularity 900 --days 730 --products BTC-EUR ETH-EUR
python -m trader fetch-history --granularity 86400 --days 900 --products BTC-EUR ETH-EUR
python -m trader backtest --granularity 900 --days 730 --strategy mtf --profile client --products BTC-EUR ETH-EUR --walk-forward
python -m trader backtest --granularity 21600 --days 730 --strategy trendhold --profile client --products BTC-EUR ETH-EUR --walk-forward
```
