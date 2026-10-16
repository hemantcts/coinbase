# Research report: can the agent make money with CHF 1 000?

*16 October 2026. Data: Coinbase Exchange BTC-EUR and ETH-EUR, 16 Oct 2024 to 16 Oct 2026, two full years.
Costs assumed: 0.6 % fee per trade plus 0.1 % slippage. All figures in EUR after costs.*

## Zusammenfassung (Deutsch)

- Die drei schnellen Strategien aus dem Konzept (Trendfolge, Momentum, Mean Reversion auf 5-Minuten- bis
  Stunden-Kerzen) **verlieren Geld**. Ihre Signale haben keine Vorhersagekraft, und die Gebühren fressen den Rest.
  Das gilt auch bei einem Zehntel der heutigen Gebühren.
- Eine **langsame Trendfolge auf 6-Stunden-Kerzen** (EMA 20/100, etwa 1–2 Trades pro Monat, weiter
  Volatilitäts-Stop) ist der einzige Ansatz, der über zwei Jahre nach Kosten positiv war: **+14 %** gegenüber
  **+2 %** für Kaufen-und-Halten bei gleicher Investitionsquote. Sie verliert in Bärenmärkten weniger als der
  Markt und nimmt in Bullenmärkten etwa die Hälfte der Bewegung mit.
- Der Preis dafür sind **zwischenzeitliche Verluste von 10–18 %**. Die im Konzept vorgesehene
  Gesamtverlustgrenze von 8 % wurde beim mittleren Risikoprofil im Test einmal erreicht; danach stand der Agent
  bis zum manuellen Neustart still. Die Grenze und die Strategie passen nicht zusammen. Das ist eine Entscheidung
  des Auftraggebers: höhere Verlusttoleranz, oder kleinere Positionen mit entsprechend kleinerem Gewinn.
- **+35 % in einem Monat** ist mit diesem Ansatz nicht erreichbar und war es in keinem der 24 Monate.
  Realistisch sind einstellige bis niedrige zweistellige Prozent pro Jahr, mit Rückschlägen von 10–20 %.
- Empfehlung: 30 Tage Paper Trading mit der langsamen Trendfolge auf einem EU-Server, danach Entscheid über
  einen Echtgeld-Pilot mit CHF 200 Einsatz. Keine weiteren Parameter-Optimierungen; sie führen nur zu
  Überanpassung.

## 1. What was tested

The agent in this repository was run on two years of real 5-minute, 1-hour and 6-hour candles through the
exact code that would trade live: the same strategies, the same risk controller, the same paper broker.
Fees and slippage were charged on every simulated fill.

| | BTC-EUR | ETH-EUR |
|---|---|---|
| start → end | 62 514 → 73 013 (**+17 %**) | 2 412 → 2 179 (**−10 %**) |
| best quarter | +44 % (Q4 2024) | +70 % (Q3 2026) |
| worst quarter | −24 % (Q4 2025) | −48 % (Q1 2025) |
| peak-to-trough | about −50 % | about −60 % |

The period contains a full cycle: a strong rise, a nine-month bear market, and a sharp rebound.
Buy-and-hold at the brief's exposure (25 % in each coin) made **+2.1 %** over the two years.

## 2. The fast strategies from the brief do not work

Trend following with volume confirmation, momentum and mean reversion were tested on 5-minute, 15-minute,
1-hour and 6-hour candles with all three risk profiles.

| timeframe | result over 90 days (bull market) | over two years |
|---|---|---|
| 5 min | −1 % to −8 %, or no trades at all | not better |
| 1 hour | −1 % to −11 % | not better |
| 6 hours | −0.2 % to −3 % | −3 % to −9 %, loss limit hit |

Two independent checks explain why:

1. **The signals have no predictive power.** For every raw BUY signal the price move over the following
   hours and days was measured. The average was +0.1 % to +0.4 % per day, the same as the market's own
   drift. A signal that predicts nothing cannot pay a 1.4 % round trip.
2. **The exits lose by design.** A fixed 2 % stop and 4 % target on hourly crypto is hit by noise: every
   winning trade earned +6.39, every losing one cost −8.41, and 59 % lost. The bot was invested only 13 % of
   the time while the market rose 31 %.

Cutting the fee to 0.1 % does not rescue them. This is not a fee problem; it is an absence of edge.

## 3. What does work, within limits: slow trend-hold on 6-hour candles

Rule: be long while the 20-period EMA is above the 100-period EMA (about 5 days over 25 days on 6-hour
candles) and the price is above the fast EMA. Exit when the EMAs cross back. A trailing stop sits six ATR
below the price, which in practice fires rarely. No profit target.

### Two years, medium profile (25 % per coin, 60 % max invested, 8 % total loss limit)

| | net | peak-to-trough DD | worst vs start | trades | fees | time invested |
|---|---|---|---|---|---|---|
| trend-hold 6h 20/100 | **+14.1 %** | 17.6 % | −2.2 % | 37 | 113 | 53 % |
| buy & hold 2 × 25 % | +2.1 % | ~30 % | | 0 | 0 | 50 % |
| with 0.4 % maker fee instead of 0.6 % | +17.9 % | 16.1 % | | 37 | 76 | 53 % |

### Consistency check, half-year by half-year

The setting was chosen because it was never the best and never the worst, not because it won:

| window | market (2 × 25 %) | trend-hold 6h 20/100 | DD |
|---|---|---|---|
| Oct 24 – Apr 25 | −6.0 % | −1.7 % | 12.8 % |
| Apr 25 – Oct 25 | +40.7 % | +14.0 % | 8.2 % |
| Oct 25 – Apr 26 | −17.4 % | −6.9 % | 9.2 % |
| Apr 26 – Oct 26 | +4.4 % | +9.7 % | 6.1 % |

It gives up roughly half of the upside and avoids more than half of the downside. That is what trend
following is supposed to do, and it is the whole value proposition: not "more profit in a bull market" but
"survive the bear market with capital intact".

The setting I liked best after the first four months of data (EMA 12/36) turned out to be the **worst** of
the candidates over two years (−8 %). That is the strongest argument in this report against further tuning.

### The three risk profiles over two years

| profile | position / invested | net | worst vs start | DD | trades |
|---|---|---|---|---|---|
| low | 15 % / 40 % | +5.4 % | −1.8 % | 11.9 % | 56 |
| medium | 25 % / 60 % | +14.1 % | −2.2 % | 17.6 % | 37 |
| high | 35 % / 80 % | +28.5 % | −4.8 % | 23.5 % | 32 |

## 4. The loss limit and the strategy do not fit together

The out-of-sample test (last ten months, starting straight into the bear market) is the honest one:

| profile | net | worst vs start | what happened |
|---|---|---|---|
| low | +1.7 % | −4.6 % | survived, small gain |
| medium | **−8.1 %** | −8.1 % | hit the 8 % total loss limit after 7 trades, then stood still for 8 months |
| high | +9.9 % | −11.5 % | survived (its limit is 15 %) |

Crypto moves 10–20 % against you even inside a healthy uptrend. With half the account invested that is
5–10 % on the account. An 8 % permanent stop will therefore trigger sooner or later, and whether the agent
then earns anything depends entirely on when the owner restarts it. Three ways to resolve this, all of them
the client's call:

1. **Raise the loss limit** to about 15 % and accept that the account can be at −15 % for months.
2. **Keep 8 % and use the low profile.** Expected result in the order of +3 to +6 % a year on CHF 1 000,
   i.e. CHF 30–60, minus the server.
3. **Keep 8 % as a stop but define the restart rule in advance** (for example: after 30 days, if the trend
   filter is positive again). Then the limit is a pause, not a death sentence, and the backtest with that rule
   is the +14 % figure above.

## 5. What "+35 % in a month" would require

In the 24 months tested, the best single month for buy-and-hold BTC was about +25 %. No rule-based
strategy on 6-hour or slower candles captured more than part of that, and anything faster lost to fees.
Reaching +35 % in a month with CHF 1 000 requires leverage, concentration in a single volatile coin, or
luck. All three can just as easily produce −35 %. The client should hear this before any money moves.

## 6. Recommendation and next steps

1. Fix the strategy at trend-hold 6h EMA 20/100, 6 × ATR trailing stop. No further optimisation.
2. Client decides the profile and the loss-limit policy (section 4). My suggestion: medium with option 3.
3. Client reads the real fee tier from the Coinbase account; if the maker fee is lower, the live broker
   switches to post-only limit orders (worth about +4 points over two years).
4. Run paper trading on an EU VPS for at least 30 days. The automatic gate then checks continuity,
   error rate, duplicate orders, honoured halts and FX recording.
5. If the gate passes: live pilot with CHF 100 per position, CHF 200 invested, for one month, then review.
6. Expectation for the client, in writing: single-digit to low double-digit percent per year, with
   drawdowns of 10–20 % along the way, and a real chance of a losing year.

## Reproduce

```bash
python -m trader import-history
python -m trader backtest --strategy all --profile medium --days 730 --granularity 21600
python -m trader backtest --strategy trendhold --profile all --days 730 --granularity 21600 --walk-forward
python -m trader backtest --strategy trendhold --profile medium --days 730 --granularity 21600 --fee 0.004
```

## 7. Decisions taken on 16 October 2026

The client delegated the two open settings ("do it yourself"). Configured defaults, each one line in `.env`:

| setting | decision | why |
|---|---|---|
| fees | maker 0.6 %, taker 1.2 % (`FEE_RATE`, `TAKER_FEE_RATE`) | Coinbase's entry tier, the worst case. Entries and planned exits are post-only limit orders at the maker rate; only stop-loss and flatten pay taker. Live mode reads the real tier from the API and never assumes a cheaper one. |
| risk profile | medium (`RISK_PROFILE`) | 25 % per coin, 60 % invested, 2 % daily and 8 % total loss limits, as in the brief. |
| after a total-loss stop | pause 30 days, then resume with the limit re-based (`HALT_RESET_DAYS=30`) | Keeps the client's 8 % number as a circuit breaker without the bot standing still for months. Set to 0 for a manual restart. |

Two-year result with these settings (maker 0.6 % in, taker 1.2 % on stops, 30-day pause):

| profile | net | worst vs start | DD | trades | fees |
|---|---|---|---|---|---|
| low | +1.0 % | −3.9 % | 12.4 % | 47 | 118 |
| **medium** | **+10.2 %** | −2.9 % | 18.6 % | 35 | 137 |
| high | +27.0 % | −5.0 % | 24.2 % | 32 | 145 |

Out-of-sample (last ten months), medium: −2.4 % with a −12 % dip during the bear market, a 30-day
pause, and recovery, against −13.5 % for the market. At the next fee tier (maker 0.35 %, taker 0.75 %)
the two-year figure is +16.8 %.
