# Changes

## 0.2 — 2026-11-10

- Add `read_many` for 1–5 distinct ranks, one body reservation per search.
- Reuse one offline VM boot for a multi-page body batch; isolate each worker.
- Bound UTF-8 batch output and validate every page source, ID and input hash.
- Add live CLI mutual exclusion without stealing stale locks.
- Permit narrowly checked removal of a leading site operator from provider echo.
- Preserve configurable Windows runtime, fixture mode and legacy `read`.
- Add two-page portable demo and synthetic boundary/adaptation tests.

No resident VM, dynamic browser, PDF support, new dependencies or new service
permissions. No private runtime configuration, logs or receipts are included.
