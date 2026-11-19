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

## Screenshot port in 0.3

The bounded reference path covers trusted operator configuration, guarded
host acquisition, scanned file binding, PNG and result validation, and
isolated VM cleanup. Its release checks exercise the public code with
synthetic inputs and mocked controls. Earlier live testing used a separately
configured local runtime and a different source version. A fresh public
runtime and live deployment remain to be independently reproduced.

## Screenshot review fixes in 0.3.1

The offline tests inspect the shared curl invocation, simulate overlapping
captures and VM shutdown states, and exercise content binding for raw headers
and the canonical replay map. They also cover the first document base and
same-origin checks. Results for the exact source freeze are recorded in
[RELEASE_CHECKS.json](../RELEASE_CHECKS.json).

## Timing and screenshot readback in 0.3.2

The clean copy passed 68 tests in ordinary Python and 68 in optimized Python.
Injected failures in either mode stopped the CLI before entering the runtime.
The table shows medians from one Windows host and four runs per arm,
interleaved in ABBA ABBA order.

| Offline comparison | Serial | Parallel |
| --- | ---: | ---: |
| Same selftest workload | 4.952 s | 2.529 s |
| CLI screenshot diagnostic with no runtime configured | 4.997 s | 2.817 s |

These measurements cover selftests and the diagnostic path. End-to-end
screenshot latency with website acquisition remains unmeasured for this
release.

One fixed synthetic page was fed in at the host acquisition boundary. The
rest of the screenshot path ran with the public source, actual host scans and
a provisioned offline VirtualBox guest. It returned three verified PNGs and
the VM powered off with its task disc detached. A reader agent received only
the PNGs in a fresh context. It read the Chinese heading and bottom table
value correctly. The middle section showed its loading placeholder in the
PNG and text excerpt. Its marker required the middle of the page to enter the
viewport, and the renderer jumped directly to the end.

Two of the three expected fields passed this end-to-end content check.
`SCREENSHOT_READY`, `resource_complete=true`, zero blocked requests and the
two false truncation flags did not identify the missed content. The result
covers one controlled page with zero website requests. The evidence uses the
exact frozen source identifier retained in
[RELEASE_CHECKS.json](../RELEASE_CHECKS.json).

## Experimental screenshot coverage and recovery

The released v0.3.2 renderer, which jumps to the page end, served as the
baseline. The experimental renderer visits overlapping viewports in steps
of up to 550 pixels. It uses the same 750-pixel viewport and 6,000-pixel
capture limit. The stepped renderer remains an experimental candidate.

### Content coverage

Four fixed synthetic pages each ran once per renderer on the same offline
VirtualBox guest. They covered viewport-triggered content, a long table, a
fixed footer, and small Chinese text. The same capture, scan, and teardown
checks passed in all eight runs. Two reader agents saw only the PNGs and the
field questions. Each agent saw one renderer per page, with the assignment
crossed between pages.

| Measure | Jump to the end | Stepped scrolling |
| --- | ---: | ---: |
| Exact answers to required fields | 10/11 | 11/11 |
| Required fields missing from both the PNG readback and excerpt, with no warning | 1 | 0 |
| Long-page runs with the expected vertical truncation flag | 1/1 | 1/1 |

The added field required entering a middle viewport. The baseline showed a
loading placeholder while reporting `SCREENSHOT_READY` and
`resource_complete=true`. Both long-page runs reported vertical truncation.
The field beyond the capture limit was outside the images. The four paired
end-to-end time differences had a median of −7.0 seconds for the stepped
renderer. Each page had one pair, and guest renderer times varied across
baseline runs, so estimating the speed effect needs repeated pairs.

### Live timing and controlled failures

The first live attempt stopped at the broker's parse/DNS check, before any
HTTP GET or VM start. Once the system resolver returned an address for the
target, a separate series on the same host and snapshot completed three
captures of the [same demo page](https://quotes.toscrape.com/js/).
Their end-to-end times were 36.67, 36.08, and 35.77 seconds. Each used the
same four same-origin responses, produced a full image with ten verified
quote cards, and blocked one cross-origin font stylesheet.

| Stage | Median for the three successful captures |
| --- | ---: |
| Request end to end | 36.08 s |
| Host acquisition and scan | 8.66 s |
| VM startup through result validation | 19.93 s |
| Guest renderer time, included in the VM stage | 2.79 s |
| VM cleanup | 1.42 s |

A local acquisition refusal stopped before scanning and VM start. In a
separate local trial, a forced stop after attaching the current job disc
exercised the real VM cleanup. The driver powered off the guest, detached
the disc, and released its locks. A subsequent synthetic capture on the
same frozen candidate succeeded. These observations cover four controlled
pages and one public demo site on one host.
