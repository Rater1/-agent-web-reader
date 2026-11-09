# Historical 0.1 publication review

This review covers the original 0.1 freeze only. The 0.2 batch changes have
author synthetic tests, not a new separate-context review.

Reviewed source freeze:

```text
8b6bb97fc1aa22dbf8c04666e98bbde11fc2eefea5530c2211e0645f6519d68c
```

A separate Codex context reviewed the publication adaptations and constructed
its own synthetic probes. This is separation of author/reviewer context, not
DS execution or external certification. No real key, registry access, network,
VM or scanner was used by the probes.

Seven groups passed under both normal and optimized Python:

- valid fixture search/read, with only one read allowed;
- fixture state wrapped in a fabricated live-shaped receipt: rejected before
  config/key/backend/read reservation;
- actual missing-runtime loader: stopped before key/budget/backend;
- source-set, source-hash and selftest failures: protected callback not called;
- synthetic valid runtime wiring: configured VM/curl reached fake adapters,
  without a credential in child environment; fake live search/read succeeded;
- actual entry's unknown-error path: one JSON stop with no synthetic secret;
- reviewed source identity unchanged during the probes.

No new blocking defect was found in that limited adaptation review. The author's
19-test host suite and clean-extraction fixture checks are separate evidence.
The public live runtime remains unexecuted. Source hashes are local integrity
checks, not authenticated execution or an audit chain.
