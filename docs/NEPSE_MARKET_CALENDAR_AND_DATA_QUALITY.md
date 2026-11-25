# NEPSE market calendar and data-quality controls

## Effective-dated weekly trading schedule

The trust-score gap check and `MarketCalendar` use the following weekly schedule. The dates below describe weekly closures only; official exchange-declared holidays are handled separately.

| Effective period | Trading days | Weekly closures |
|---|---|---|
| Before 15 May 2022 | Sunday–Thursday | Friday, Saturday |
| 15 May–14 June 2022 | Monday–Friday | Saturday, Sunday |
| 15 June–16 September 2022 | Sunday–Friday | Saturday |
| 17 September 2022–5 April 2026 | Sunday–Thursday | Friday, Saturday |
| From 6 April 2026 | Monday–Friday | Saturday, Sunday |

### Source trail

- 15 May 2022 weekend change and Friday opening: [Khabarhub, 6 May 2022](https://english.khabarhub.com/2022/06/250571/) and [ShareSansar, 22 May 2022](https://www.sharesansar.com/newsdetail/nepse-lost-11533-points-491-this-week-weekly-summary-with-technical-analysis-sector-comparison-major-highlights-and-more-2022-05-22).
- Six-day Sunday–Friday schedule from 15 June 2022: [The Himalayan Times, 13 June 2022](https://thehimalayantimes.com/business/nepse-to-operate-six-days-a-week-effective-june-15).
- Friday closure resumed from Ashoj 2079: [ShareSansar, 4 September 2022](https://www.sharesansar.com/newsdetail/nepse-decides-to-close-stock-trading-on-fridays-from-ashoj-2022-09-04). Ashoj 1, 2079 was 17 September 2022.
- Monday–Friday trading effective 6 April 2026: [The Himalayan Times, 8 April 2026](https://thehimalayantimes.com/business/nepse-shifts-to-mondayfriday-trading-week) and [NEPSE notice reported by ShareSansar, 8 April 2026](https://www.sharesansar.com/newsdetail/nepal-stock-exchange-opens-monday-to-friday-under-new-government-timings-2026-04-08).

These rules must be treated as versioned market-calendar data. Do not replace them with a single current weekday rule for historical research.

## Data-quality calculation

The current gap logic:
- Looks only at bars on or before the target date, to prevent future observations from contaminating historical trust evaluation.
- Inspects the most recent 30 observed bars and counts only dates that would have been trading sessions under the effective-dated weekly schedule.
- Excludes market holidays only when a matching holiday row is present in `holiday_calendar`.
- Keeps raw price history unchanged. It does not auto-correct prices or corporate actions.

## Holiday-calendar limitation

The live Neon `holiday_calendar` table was empty at the latest read-only audit on 25 November 2026. Therefore, weekday public-holiday exclusions are not yet complete. Until a verified NEPSE/Government holiday calendar is loaded and validated:
- Missing-weekday calculations can over-count missing sessions that were actually public holidays.
- Do not treat the resulting trust scores as production-grade.
- Keep feature, signal and backtest generation in SAFE_MODE; do not bypass the existing gate to force signals.

An authoritative holiday source must be verified and recorded with effective dates before this limitation is lifted. Calendarific is optional in configuration and is not a substitute for persisted, reviewed exchange holiday closures.

## Ingestion evidence and source registry

New equity imports record a versioned rejection summary in the existing `ingestion_logs.errors` JSON field:
- `rejected_by_symbol_date`: row-rejection counts for each symbol/date.
- `invalid_date_rows_by_symbol`: rejected rows whose source date cannot be parsed.
- `download_errors`: source download failures.

Future equity and index imports are linked to separate `data_sources` records. Older ingestion logs with a null `source_id` and legacy error payloads remain readable; the code does not fabricate symbol/date reject evidence for historical runs where it was never captured.

The trust calculation resolves the newest versioned import covering the target symbol/date, rather than summing repeated refreshes. This prevents the same rejected source row from being counted repeatedly.

## Validation and release requirements

Before enabling trust-scored features or recommendations:
1. Populate `holiday_calendar` from an authoritative, date-verified NEPSE closure list.
2. Reconcile import logs and rejected-row evidence after a controlled refresh.
3. Revalidate the stale symbols, source coverage and benchmark same-session guard.
4. Review data-trust distributions, suspicious discontinuities and corporate-action limitations.
5. Keep any signal/backtest result marked research-only until validation and independent backtesting are complete.
