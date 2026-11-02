# 반반매매 offline research engine

Vendored from the user-supplied friend source at pinned commit
`26673cef86f22ddce021f77c793e15db536d9874`, originally preserved under
`reports/atr_r2_review_2026-10-31/friend_source_26673ce/research`.

Only the recursive dependencies of the audited ATR/R2 backtester, settings,
and explicit OHLC snapshot provider are included. Local imports are changed to
package-relative imports. The wall-clock live-session skip is disabled because
the wrapper accepts completed historical sessions only; trading formulas are unchanged. `UPSTREAM.json`
records original and packaged hashes. The original source remains untouched.

The public application boundary is `banban_strategy.py`. It requires explicit
SOXL/QQQ bars, cuts both histories to the requested completed date, and supplies
the offline `SnapshotProvider`; implicit live market access raises an error.
This package has no order-submission or account-writing capability.

Dependencies: pandas, numpy, pytz, exchange_calendars. The source's modeled
commission is a percentage (`0.05` means 0.05% per side); the application wrapper
uses a decimal fraction (`0.0005`) and converts it at the boundary.

The engine models ATR intraday entry from daily high at open × 1.015, next-open
ATR exits, R2 close/LOC fills, and monthly first-session rebalancing. These are
historical simulation assumptions, not broker-confirmed executions.
