# Changes

## 0.3.2 — 2026-11-19

- Record accepted CLI request time and screenshot phase timings.
- Run ordinary and optimized Python selftests in two concurrent groups, with both required before processing the request.
- Record a real offline VM readback case in which the screenshot returned successfully but skipped a viewport-triggered middle section.

## 0.3.1 — 2026-11-19

- Disable curl URL globbing for all broker requests.
- Serialize screenshot jobs by VM UUID, protect boot media from serial truncation, and wait for power-off before detaching the job disc.
- Use canonical browser URL keys, honor supported document base URLs, and retain approved response policy headers in offline replay.
- Reject attachments and encoded screenshot responses before replay.

## 0.3 — 2026-11-19

- Add an optional public HTTPS screenshot operation using an operator-provisioned offline browser VM.
- Return three bounded PNG views, a text excerpt, and blocked-resource and truncation indicators on success.
- Restore one clean snapshot per screenshot. Power off the guest and detach the task disc when done.
- Add a network-free screenshot diagnostic and document the trusted Windows setup.
- Keep screenshot reservations separate from search and static reads.

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
