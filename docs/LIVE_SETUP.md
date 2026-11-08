# Windows live reference setup

The portable fixture demo is the reproducible first step. This live recipe is
for an operator already familiar with VirtualBox and Defender; the public
adaptation has not been rerun with a newly provisioned VM. No installer runs
when you extract or test the source. Do not configure a production VM here.

## 1. Obtain the separate materials

Use a writable Windows source directory and an **external** runtime directory,
for example `C:/agent-web-runtime` (not inside this source bundle, not a drive
root or an ancestor of the source bundle). Paths must not contain apostrophes
or control characters because the VISO format uses quoted mappings.

Install/verify VirtualBox separately. No Extension Pack is needed. The inherited
driver expects a diskless, BIOS Linux VM, 1 CPU, 768 MiB RAM, serial COM1 at
`0x3f8` IRQ 4, boot from optical media, with no virtual hard disk. The optical
controller must be named `OfflineOptical` (IDE/PIIX4):

- port 0 / device 0: `download/alpine-virt-3.24.4-x86_64.iso` under the runtime root;
- port 1 / device 0: `public_inputs.viso` under the runtime root;
- UART1 file: `serial_result.txt` under the runtime root;
- all eight NIC slots `none`, clipboard and drag/drop disabled, no shared folders;
- no USB controllers, audio, remote display or clipboard file transfers;
- powered off before each operation. Use a dedicated disposable VM, not an
  existing work VM. The driver starts it, logs in as root on the live image,
  mounts the read-only optical capsule, then powers it off.

The base `public_inputs.viso` is an empty or synthetic read-only optical image
description. It and `serial_result.txt` are the fixed attachments restored after
each run. Provision them before starting. The runtime must contain only public
materials, and the VM must not have access to private files.

Pinned ISO SHA256:

```text
157d353f8e54252cad2d9812a0d56bb7fa2231fc154a110e1d180d7480ef8b4f
```

Official source:
`https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/x86_64/alpine-virt-3.24.4-x86_64.iso`

The 21 required signed APK files belong in
`search_extract_v01/packages/` under the external runtime root. Exact names,
versions, official URLs, SHA256 values and license metadata are in:

```text
poc/review_probes/web_research_boundary_20261108/search_extract_v01/apk_download_receipt.json
```

This pinned set includes Python 3.14.10 and Beautiful Soup 4.15.0. Do not
silently substitute newer packages; that changes the tested runtime and content
binding. Missing or no-longer-available artifacts stop this reference setup.
No binaries are included in this repository.

## 2. Bind a full scan of the exact package directory

The inherited gate records a full Defender/event-log scan and the current file
tree's content hash. Its `--pi-exception` name is a legacy flag for explicit
operator approval of guest code; it does not identify a person or grant itself
authority. The following is a template for your chosen runtime directory:

```powershell
python -I -S -B poc/download_gate/dl_gate.py declare --kind other --target "Offline guest APK set" --source dl-cdn.alpinelinux.org --size-mb 20 --scratch C:/agent-web-runtime/search_extract_v01/packages --tag web-parser-apks-20261108 --executes-code --pi-exception --reason "Operator approved signed packages for the offline parser VM"
python -I -S -B poc/download_gate/dl_gate.py scan --path C:/agent-web-runtime/search_extract_v01/packages --tag web-parser-apks-20261108
```

The ledger remains local and ignored. The driver requires `OK`, full content
binding, event-log evidence and the pinned package hash; a weak scan, unreadable
event log or changed file set stops execution. A scan is not a guarantee a
program is harmless. Do not disable Defender to make a failed scan pass.

## 3. Configure trusted host values

Copy `runtime.example.json` to `runtime.local.json`. Set:

- your external runtime root, VirtualBox path, dedicated VM name;
- your verified curl path and SHA256 (PowerShell `Get-FileHash -Algorithm SHA256`);
- `proxy_url`: your loopback HTTP proxy, or empty string for direct transport;
- `baseline_vm`: null, or the name of an existing separate powered-off comparison
  VM whose configuration you also want checked;
- `operator_approved_live_runtime`: true only after you have prepared the above.

Configuration does not contain an API key. Missing/invalid configuration stops
before the entry's credential getter, budget reservation and backend call.
If a baseline VM is omitted, the receipt records its comparison as null.

Configure your own Tavily key as `TAVILY_API_KEY` privately in the process
environment or the current user's Windows Environment value. No shell snippet
with a literal key is needed in this repository. A fresh process environment may
be needed after changing an environment variable; the Windows user-value
fallback supports an already-running launcher without copying another process's
environment. Do not put the key in a command argument or send it to an agent.

## 4. Run one bounded request

Send a public query to `python -I -S -B agent_web.py`, then read one returned
candidate by search_id/rank. Expect separate acquisition/scan/guest/cleanup
stages for search and page read, so this reference can take substantially longer
than an API call alone. No performance comparison is claimed.

On any `STOPPED`, inspect the private local receipt. It may mean a request was
sent but no final result was returned. Never automatically retry to discover
what happened. This implementation has no general result cache and deliberately
does not refund failed reservations. Keep state and runtime receipts private.
