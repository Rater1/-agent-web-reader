# Validation scope

## Public adaptation

The release acceptance is a clean-directory fixture run, synthetic host tests,
source closure/hash checks, archive allowlist checks and focused review of the
publication changes. No real key, search/GET, scanner, VM or model is used in
those tests. Fixture examples do not validate the live Windows setup.

The host suite includes positive fixture search/read; invalid/duplicate JSON;
URL/rank scope; expired/tampered state; atomic read and daily search reservation;
failed-read consumption; missing key; configuration-before-key ordering;
source/selftest failure blocking; fixture-to-live promotion rejection; and a
fully synthetic live-shaped backend/receipt control. Tests use unittest checks
in both normal and optimized Python rather than relying on assert statements.

Version 0.2 has 19 legacy host cases plus 17 batch/public-adaptation cases,
run in normal and optimized Python. The fixture demo exercises two pages after
clean extraction. These are synthetic checks, not live VM measurements. The
previous version had a separate-context review; it does not review the new
batch changes. See [historical review scope](REVIEW.md).

Guest tests are included for the provisioned runtime. They exercise the actual
parser but require its separate Beautiful Soup dependency. A passed portable
host suite must not be described as a new guest or live integration test.

## What motivated the boundaries

Development review found three useful failures:

1. Scanning only a later canonical copy missed the raw-download directory.
   Both now belong to the declared incoming scan tree.
2. A hidden terminal prompt could fall back to echoed input. The public Agent
   entry uses only operator-configured credentials, without interactive input.
3. A local synthetic-session flag could be changed to live without binding the
   original receipt's mode. Read now checks receipt provenance before the read
   reservation; public fixtures use a different schema and identity altogether.

These are component consistency cases, not proof of arbitrary-host isolation.
The original private source version had a bounded live integration smoke, but
it contained local paths and a different freeze. No private receipts, real
search results, screenshots or identifiers are redistributed. That historical
run is not a live result for this public adaptation.

## Open work

- Reproduce the public live recipe on a separate, newly provisioned host.
- Improve excerpt selection on navigation-heavy pages without confusing
  excerpts with full documents.
- Independently reproduce the narrow site-operator echo policy with live
  provider responses in the public configurable runtime.
- Compare usable output, latency and cost under a separately designed protocol
  if a performance claim is needed.

No web benchmark, attack-success rate, prompt-injection detection claim or
commercial service certification is reported by this bundle.

## Batch checks in 0.2

Covers ordered multi-page output; duplicate/invalid ranks; no appended read;
failure consuming the reservation; wrong second source/hash/ID; receipt tamper;
second GET failure stopping later GETs and VM work; lock release; source and
selftest failure blocking; independent clean workers and stop-on-parse-failure;
five Chinese excerpts within the frame cap; frame overflow; job-count limit;
narrow query normalization; fixture/live separation; runtime-before-reservation;
and the real public class hierarchy retaining the 12 MiB batch declaration.

Author port and checks only; no new independent review or public live rerun.
The related private implementation observed about 50–51 seconds for a two-page,
single-boot run, compared with earlier 48–49 second single-page cached runs.
Those were different versions and a limited engineering observation, not a
repeated benchmark or a latency guarantee for this public adaptation.
