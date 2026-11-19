# Agent Web Reader

A small JSON tool for public search, bounded text excerpts, and screenshots of
public HTTPS pages. The Windows reference path acquires and scans bytes on the
host, then uses separate diskless Linux VMs for static parsing and screenshot
rendering. Both guests run without network interfaces. The search credential
stays on the trusted host.

**Experimental source candidate.** The portable fixture demo works without an
API key, VM, scanner, proxy, or third-party Python package. Live operation needs
the Windows setup below; this is not a one-command cross-platform sandbox.

[中文说明](README.zh-CN.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Windows live setup](docs/LIVE_SETUP.md) · [Agent routing](docs/AGENT_USAGE.md) ·
[Screenshot setup](docs/SCREENSHOT_SETUP.md) ·
[Tests and limitations](docs/VALIDATION.md)

## Try it offline

Use Python 3.10 or newer in a writable extracted directory:

```console
python -I -S -B demo.py
python -I -S -B poc/applications/public_web_agent_v01/test_agent.py
python -I -S -B -O poc/applications/public_web_agent_v01/test_agent.py
python -I -S -B poc/applications/public_web_agent_v01/test_batch.py
python -I -S -B -O poc/applications/public_web_agent_v01/test_batch.py
```

The demo prints a `SEARCH_OK` and a two-page `READ_MANY_OK`, both with `mode: fixture` and
`source.mode: synthetic`. The page text is an included fixture. **It does not
fetch example.com, boot a VM, or produce a VM execution receipt.** Fixture state
is written under the ignored `.local/` directory.

## Agent interface

Send one UTF-8 JSON object on stdin to `python -I -S -B agent_web.py`.
The process returns one JSON object on stdout; a stop uses exit code 1.
Use `--fixture` for the offline example; omit it only after live setup.

```json
{"op":"search","query":"Python URL parsing documentation"}
```

Keep the returned `search_id`, then choose up to five distinct ranks in one batch:

```json
{"op":"read_many","search_id":"<32 hexadecimal characters>","ranks":[1,2]}
```

`pages` follows the requested rank order. The single-page interface remains compatible:

```json
{"op":"read","search_id":"<32 hexadecimal characters>","rank":1}
```

Search returns at most five candidates. Read accepts only a rank from that
search, not an arbitrary URL, local path, header, cookie, or command. A session
expires after two hours. One body-read reservation is allowed per search ID: either one `read` or one
`read_many` batch of 1–5 pages. Select the complete batch first; a failure consumes
it, and further pages cannot be appended to the same session. The default local cap is 20 search
reservations per local calendar day. This is not an account-wide billing cap.

Use a separate screenshot request for a known public HTTPS URL.

```json
{"op":"screenshot","url":"https://quotes.toscrape.com/js/"}
```

Inspect the local `SCREENSHOT_READY` PNG paths alongside the excerpt and its
resource and truncation flags. The default cap is four screenshot reservations
per local day. Each request restores and powers off the dedicated screenshot
VM. No search credential or search ID is needed. [Setup and limits](docs/SCREENSHOT_SETUP.md)

## Why this exists

Search APIs supply discovery; HTML parsers supply text extraction. This project
assembles those parts into an agent-facing workflow with explicit acquisition,
scan/content binding, offline parsing, bounded output, and failure behavior.
It is a reference for inspecting those boundaries, not a new search engine.

The project's own source is MIT licensed. Operators supply the configured VM
and host transport. Keyword search requires an operator-provided Tavily account
and its service terms. [Third-party notices](THIRD_PARTY_NOTICES.md)

## Live path and current limits

Live requires Windows, VirtualBox, Microsoft Defender event-log scan evidence,
an approved local curl binary, the pinned Alpine image and APK set, and an
explicit `runtime.local.json`. See [the setup recipe](docs/LIVE_SETUP.md).
No credential is requested by the fixture demo. There is no automatic installer.

The parser supports static HTTPS UTF-8 HTML discovered by public-keyword
search. Screenshot rendering supports one public HTTPS HTML page and its
bounded initial same-origin JavaScript and CSS. Both paths exclude redirects,
login pages, PDF, attachments, and compressed page responses. HTML input is
limited to 1 MiB and the text excerpt to 4,000 characters. Source and truncation
fields remain visible.

Query binding permits only removal of a leading `site:host[/path]` operator
from the provider echo, with all other terms unchanged and every result in that
exact host/path scope. Both requested and provider queries remain visible. Other
changes stop the request, which may already have been charged; do not repeat it.

Multi-page live reads fetch bounded pages on the host, scan the inputs, and use
one VM boot for the entire body batch, with a separate bounded worker per page.
Search is a separate VM cycle; there is no resident service. A busy or stale CLI
lock stops new live operations rather than being automatically removed.

The public bundle is validated with fixture and host-side checks. See the
[validation record](docs/VALIDATION.md) for the scope of earlier live evidence
and the remaining public deployment checks.

## Security boundary

The host acquisition code, operator configuration, VM manager, scanner and
agent's host privileges are trusted. A page is data and cannot directly add
URLs or commands to the fetch protocol. A zero-NIC guest limits its
available channels; it is not a proof against hypervisor compromise.

Extracted prose can still contain prompt injection. This tool does not decide
which instructions an agent should trust. An agent with unrestricted host-shell
access can bypass this CLI and read the host's credentials or files. Use
separate OS/harness permissions if that is your threat model. Local hashes and
state are consistency checks, not signed audit records or same-user tamper proof.

## Contents

The historical `poc/` layout is retained to keep thin reuse inspectable.
`PROVENANCE.json` maps copied project sources to the independent public version.
`MANIFEST.sha256` inventories the release files. Runtime `.local/`, local
configuration, raw responses, scan ledgers, images and credentials are excluded.

See [third-party notices](THIRD_PARTY_NOTICES.md) and [MIT license](LICENSE).
