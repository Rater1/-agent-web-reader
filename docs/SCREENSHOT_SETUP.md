# Windows screenshot runtime

First configure the trusted host curl path, binary hash, and transport in
`runtime.local.json` using the [Windows live setup](LIVE_SETUP.md#3-configure-trusted-host-values).
The screenshot operation also needs a dedicated VM and a private
`screenshot.local.json`. Keep its work directory outside the checkout and
separate from the static parser's runtime. Use the [example configuration](../screenshot.example.json)
as a template. Both local configuration files belong on the operator's machine.

## Prepare the guest

Create one diskless Linux VM with 3072 MiB RAM, two CPUs, optical-only boot, and
at least 4.5 GiB physical memory available on the host at run time. Set all
eight NIC slots to `none` and disable shared folders, clipboard and file
transfers, drag and drop, USB controllers, audio and remote display. Use one
two-port IDE/PIIX4 controller named `OfflineOptical`. Mount the verified Alpine
virtual boot ISO at port 0 / device 0 under the screenshot work directory. Leave
the other slots empty except while preparing the guest. Configure COM1 at
`0x3f8` / IRQ 4 in file mode with the `serial_path` from your local configuration.
Disable the other UART slots. Store the VM configuration, snapshot state and
serial file under the screenshot work directory. [VirtualBox documentation](https://www.virtualbox.org/manual/)

Prepare a complete set of compatible, signed Alpine APKs for Node.js,
`chromium-headless-shell`, fonts and their dependencies on a separate trusted
staging system. The development environment used Alpine 3.24, Node.js 24,
and the distro's Chromium headless shell. Use a verified ISO and official
repositories for your selected Alpine branch and architecture. Bind your staged
material to SHA-256 checksums and retain the signatures. The source release
supplies the guest renderer and expects the operator to provision these
dependencies. [Alpine package repositories](https://wiki.alpinelinux.org/wiki/Repositories)

Boot the clean guest from the ISO. Attach read-only setup media at port 1 /
device 0, with a signed package set under `apks/` and a `checksums.txt` file
covering that set. From the root console, verify and install the packages offline.

```sh
set -e
mkdir -p /mnt/setup
mount -o ro /dev/sr1 /mnt/setup
(
  cd /mnt/setup
  sha256sum -c checksums.txt
  apk verify apks/*.apk
  apk --no-network --repositories-file /dev/null add --force-non-repository apks/*.apk
)
test "$(ls /sys/class/net)" = lo
ip link set lo up
adduser -D browser
node --version
test -x /usr/bin/chromium-headless-shell
test ! -e /tmp/capture
test ! -e /tmp/shot_dirty
umount /mnt/setup
```

Detach the setup medium in VirtualBox. Take a snapshot while the VM is running
at the trusted root console, with no web page or browser process open. Then
power off the VM normally. The screenshot driver restores the saved running
state. It uses the `browser` account for rendering, and it expects the root
console to remain ready to accept its fixed mount command.

## Bind the saved state

Copy `screenshot.example.json` to `screenshot.local.json` and set its values
to the configuration you just provisioned. Use the actual UUID shown by
`VBoxManage snapshot <name> list --machinereadable`. Use absolute paths and
the hash of the exact snapshot XML subtree serialized by Python ElementTree.
This PowerShell projection shows the remaining fields for a powered-off VM.

```powershell
$shotVmConfig = 'C:/agent-web-screenshot/AgentWebReader-Screenshot/AgentWebReader-Screenshot.vbox'
$shotSnapshotUuid = 'REPLACE_WITH_SNAPSHOT_UUID_FROM_VIRTUALBOX'
@'
from hashlib import sha256
from pathlib import Path
import json
import sys
import xml.etree.ElementTree as ET

config = Path(sys.argv[1]).resolve()
uuid = sys.argv[2].strip("{}").lower()
node = next(
    element for element in ET.parse(config).iter()
    if element.tag.rsplit("}", 1)[-1] == "Snapshot"
    and element.get("uuid", "").strip("{}").lower() == uuid
)
state = (config.parent / node.attrib["stateFile"]).resolve()
state_hash = sha256()
with state.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        state_hash.update(chunk)
print(json.dumps({
    "vm_config_path": str(config),
    "snapshot_uuid": uuid,
    "snapshot_xml_sha256": sha256(ET.tostring(node)).hexdigest(),
    "state_path": str(state),
    "state_sha256": state_hash.hexdigest(),
    "state_bytes": state.stat().st_size,
}, indent=2))
'@ | python -I -S -B - $shotVmConfig $shotSnapshotUuid
```

Turn `prepared_without_pages` and `operator_approved_live_runtime` on after
verifying the clean state and dedicated VM. Treat any runtime mismatch as a
signal to inspect the registered VM, exact snapshot and saved bytes again.
Keep the VM powered off between requests.
Screenshot drivers targeting the same registered VM coordinate through a
UUID-named lock directory in its configuration directory, including across
checkouts. A stopped job must prove that the VM powered off and that its
task disc was detached. If the process died with a lock in place, inspect
the VM and task drive before clearing the stale lock.

VirtualBox can rewrite transient guest properties during an initial restore or
media detachment. If the snapshot hash changes, inspect the exact XML difference
while the VM is powered off. Only record a new hash after checking the intended
snapshot, saved-state bytes, isolation settings and empty task drive. Keep the
complete snapshot hash check.

## Diagnose and run

```powershell
python -I -S -B agent_web.py --diagnose-screenshot
'{"op":"screenshot","url":"https://quotes.toscrape.com/js/"}' | python -I -S -B agent_web.py
```

The diagnostic only checks local prerequisites. Each live request uses one
clean restore for one URL. A successful request returns three PNG views after
powering off and detaching its task disc. Inspect the returned paths, text and flags.
The host obtains at most one HTML page and eight initial same-origin scripts
or styles. The guest receives only the scanned job bytes. Navigation through
redirects, login, runtime API requests and cross-origin assets are outside the
operation's scope. The output can be partial even when the renderer succeeds.
Capture keys use a canonical HTTPS URL. For initial scripts and styles, the
collector honors the supported document base and still enforces the original
document origin. Responses must be inline and identity-encoded. Offline replay
preserves an allowlist of policy headers, including repeated Content Security
Policy fields. Completeness flags describe acquisition and replay observations
within this scope.
[Agent routing](AGENT_USAGE.md#screenshot-request)
