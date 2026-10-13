# Danish Demand & Procurement Desk

Forecast hourly electricity demand in Denmark's DK1 and DK2 areas, translate forecast uncertainty into procurement quantities, and follow each paper order from planning to reconciliation.

The project combines delayed-data forecasting, chronological model selection, three procurement policies, quantity limits, and a persistent SQLite audit ledger. A self-contained interactive report lets you change the area, delivery day, policy, cost scenario, and order-book snapshot.

**Research and paper simulation only.** Customer demand is a hypothetical 0.1% share of zone gross consumption. Shortage and surplus charges are assumptions, not observed imbalance prices. There is no exchange connection or live execution.

## Explore the results

Download this repository and open [reports/benchmark/report.html](reports/benchmark/report.html) in a browser. It works offline without a server or external assets. GitHub's file view shows the HTML source; download it to use the controls.

The held-out evaluation covers **1–29 September 2026**, with 696 hourly observations per zone and 29 complete paired UTC days. Model selection used earlier validation data. A mean of the same hour 2, 3, and 4 weeks earlier won in both zones.

| Selected model results | DK1 | DK2 |
|---|---:|---:|
| Mean absolute error, zone MWh | 234.71 | 81.72 |
| Weighted absolute percentage error | 8.05% | 4.89% |
| Bias, zone MWh | -166.85 | -59.02 |
| Nominal 90% interval: observed coverage | 80.17% | 85.49% |

Negative bias means understated demand. The intervals under-covered on this holdout; they should not be described as a demonstrated 90% service guarantee. In DK1, the two-week baseline did better on the test period than the selected seasonal average. The earlier validation choice is retained rather than changed after observing the test results.

At the default assumed shortage/surplus charges of **EUR 200/50 per MWh**:

| Policy | Accepted MWh | Shortage MWh | Surplus MWh | Mismatch charges | Capped orders | Fully rejected |
|---|---:|---:|---:|---:|---:|---:|
| Q50 | 3,159.55 | 118.74 | 85.67 | EUR 28,031.16 | 13 | 0 |
| Q80 | 3,311.87 | 71.39 | 190.64 | EUR 23,810.19 | 27 | 0 |
| Q90 | 3,399.18 | 63.09 | 269.64 | EUR 26,099.32 | 55 | 6 |

Capped counts include fully rejected orders. Q80 reduced mismatch charges by 15.06% relative to Q50 in this historical scenario. This is not realized profit or evidence of future savings. Increasing the buffer can use up the shared daily quantity limit: Q90 left six late DK2 hours fully uncovered. Allocation order matters.

## Run locally

Requires Python 3.11 or later. NumPy is the only runtime dependency; SQLite and the test runner are in Python's standard library.

```sh
python -m venv .venv
# Activate .venv using the command appropriate for your shell.
python -m pip install -e .
python -m unittest discover -s tests -v
python -m demanddesk.cli download --data data
python -m demanddesk.cli run --data data --out reports/my-run
```

Open `reports/my-run/report.html`. Use a fresh output directory for every run: the program refuses to overwrite an existing procurement database. The download command requests the fixed benchmark period, not an automatically rolling latest period. Downloading needs internet; tests use small offline fixtures.

The checked-in report, metrics, forecasts, order exports and Q80 event export are ready to inspect. Raw downloaded caches and SQLite databases are excluded from version control. A fresh run creates the Q80 SQLite database locally. Source revisions can change a future reproduction; metadata includes the request, record counts and SHA-256 fingerprints of the downloaded cache.

## Data and availability

Official Energinet sources:

- [GenerationProdTypeExchange](https://www.energidataservice.dk/tso-electricity/GenerationProdTypeExchange): hourly `GrossCon`, in MWh, including transmission losses.
- [DayAheadPrices](https://www.energidataservice.dk/tso-electricity/DayAheadPrices): quarter-hour day-ahead prices in EUR/MWh. Four complete quarters are averaged for an hourly price, retaining negative prices.
- [API guide](https://www.energidataservice.dk/guides/api-guides) and [demand metadata](https://api.energidataservice.dk/meta/dataset/GenerationProdTypeExchange).

The request spans 1 October 2025 to 1 October 2026, end-exclusive in the API's Danish-local date convention. Computation, splits and ledger times use UTC. The cache contains 17,520 demand rows and 70,080 quarter-hour price rows across the two areas. Demand versions are Final: 4,418; Preliminary: 12,046; Initial: 1,056.

Energinet's preliminary settlement data typically arrives after 8–10 days; the best available dataset also contains later revisions. The simulation imposes a conservative **12-day observation delay**: an hourly value is usable only after its hour has ended plus 12 days. This is a modeling assumption, not a reconstruction of publication vintages. Revised values can still contain information unavailable at the historical issue time. Original vintages would be required for a strict point-in-time replay.

The source metadata also describes a planned DataHub 3.0 methodology change in October/November 2026. The fixed benchmark precedes that change, but a future extended dataset needs review before reuse.

## Forecast design

A forecast is issued at **09:00 UTC on the preceding day** for the next UTC day's 24 delivery hours: 15–38 hours ahead. This is a research convention, not an implementation of exchange gate-closure rules or Danish-local market days.

Candidates are a two-week lag, the mean of same-hour lags at 14/21/28 days, and ridge regression with penalties 1, 10 and 100. Regression features include those available lags, the latest usable observation, recent 24-hour summary statistics, and calendar encodings. Scaling and coefficients are fitted on training data only. Outputs are floored at zero.

| Phase | Delivery period | Samples per zone | Purpose |
|---|---|---:|---|
| Training | Before 1 June 2026, after lag eligibility | 5,162 | Scaling and regression fit |
| Validation | 15 June–14 July 2026 | 720 | Select minimum-MAE candidate |
| Calibration | 28 July–17 August 2026 | 504 | Residual offsets and interval width |
| Test | 1–29 September 2026 | 696 | Final evaluation only |

Gaps ensure the preceding phase's labels have matured under the delay assumption before the next phase's first issue. Models are not refitted on validation, calibration or test labels.

Q50/Q80/Q90 quantities add the corresponding empirical signed calibration-residual quantile to the selected point forecast, then floor at zero. Thus Q50 procurement is not identical to the raw point forecast reported in the forecast metrics. The nominal 90% interval uses the finite-sample absolute-residual rank `ceil((n + 1) * 0.9)` and a nonnegative lower bound. Time dependence and drift can invalidate nominal coverage. Constant residual offsets are an approximation, not a rich conditional quantile model.

## Procurement and paper trade management

Each policy has its own book. Orders are capped at **4 MWh per zone-hour** and **120 MWh across both zones per UTC delivery day**. Within an issue batch, all DK1 delivery hours consume capacity before DK2, with hours ordered within each zone. This area-first allocation prioritizes DK1 when the shared daily limit binds. Quantities are clipped rather than jointly optimized across hours. The system also provides a kill switch, duplicate protection, input validation and chronological event checks.

An accepted nomination moves through `PROPOSED → APPROVED → OPEN → SETTLED`. A rejected nomination moves through `REJECTED → REJECTED_RECONCILED`; its uncovered actual demand still creates a cost. The paper desk assumes accepted quantities can be nominated; it does not simulate order matching, liquidity, partial exchange fills or fees. Reconciliation occurs at delivery-hour end plus 12 days. The report's planning snapshot hides future actuals and shows OPEN/REJECTED states; the later snapshot shows the corresponding reconciled states.

For actual energy `A`, accepted planned energy `Q`, hourly spot price `P`, shortage charge `Cs`, and surplus charge `Co`:

```text
shortage = max(A - Q, 0)
surplus  = max(Q - A, 0)
cost     = A * P + shortage * Cs + surplus * Co
```

This is equivalent to procuring Q at spot, buying shortages at `P + Cs`, and selling surplus at `P - Co`. Prices are used for ex-post accounting, not as forecast-time inputs. The common actual-demand spot component cancels in comparisons between policies, so the report emphasizes mismatch charges.

The report compares `(Cs, Co)` of `(50, 50)`, `(200, 50)` and `(450, 50)` EUR/MWh. Their unconstrained critical quantiles `Cs / (Cs + Co)` are 0.5, 0.8 and 0.9. Quantity caps and fixed residual offsets mean those rules need not minimize realized constrained costs. In the high-shortage scenario, Q90's uncapped advantage disappears after quantity limits.

The saved SQLite database and order CSVs use the default EUR 200/50 charges. Alternative cost scenarios are recalculated for the report from the same quantities and actual demand. SQLite persists orders and audit events; runtime risk settings are not a separately persisted policy configuration.

## Validation and structure

**31 local tests passed.** Tests cover missing and duplicate observations, negative price aggregation, cache fingerprints, delay boundaries, future-data invariance, training-only scaling, interval calibration, phase gaps, accounting, shared caps, rejection costs, duplicate and premature settlement guards, persistence, event chronology and report escaping. Browser checks confirmed all report selectors, rejected orders, later reconciliation and an empty warning/error log.

GitHub Actions passed on Python 3.11, 3.12 and 3.13 for the initial project upload. Inspect the Actions tab for the latest execution status.

```text
demanddesk/data.py      Official downloads, cache integrity and hourly alignment
demanddesk/model.py     Availability-aware features, selection and calibration
demanddesk/book.py      SQLite paper orders, risk limits and event audit
demanddesk/pipeline.py  Chronological simulation, cost scenarios and exports
demanddesk/report.py    Standalone interactive report
demanddesk/cli.py       Download and run commands
tests/                 Offline behavioral and leakage checks
reports/benchmark/     Published historical benchmark and audit exports
```

This short holdout does not establish performance across seasons. Zone-level gross load scaled to a customer share omits customer-specific behavior. Weather forecasts, holidays beyond calendar encodings, metering revisions, network constraints, execution costs and actual imbalance settlement rules are not modeled. The software is MIT licensed; source data remains subject to Energinet's terms.
