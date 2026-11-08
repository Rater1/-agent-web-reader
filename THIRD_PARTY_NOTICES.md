# Third-party components and services

MIT covers this bundle's project-owned source and synthetic fixtures. No
third-party implementation source, runtime binary, package archive or VM image
is bundled. Imports of the Python standard library do not make the Python
runtime part of this distribution.

| Component | Role | Distribution / source |
|---|---|---|
| Python | Host and guest runtime | Separate; [official license](https://docs.python.org/3/license.html) |
| Beautiful Soup 4 | Guest HTML parsing | Separate; MIT is stated on the [official project page](https://www.crummy.com/software/BeautifulSoup/); exact APK metadata is included |
| Alpine Linux / APK dependencies | Diskless guest and signed packages | Separate; per-package license metadata and official URLs are in `apk_download_receipt.json`; not one blanket license |
| Oracle VirtualBox | VM boundary | Separate; consult the [official licensing page](https://www.virtualbox.org/wiki/Licensing_FAQ) for the version you install; no Extension Pack is required or included |
| Microsoft Windows / Defender / curl | Trusted host acquisition and scan | Separate OS components; operators verify their local binary and permissions |
| Tavily | Search API | External service with the operator's own account; [service terms](https://www.tavily.com/terms) apply; no key, SDK, account access or service sublicense is provided |

The package list describes a pinned reference environment, not an automatic
download permission or a guarantee the artifacts remain available. The release
preparation did not reinstall these dependencies. Some official pages could not
be retrieved during the final check; links are provenance pointers, not a claim
that all versions' license texts have been independently audited.

No Stripe Smokescreen implementation is included. Prior comparison with that
project was architectural research; this bundle reuses the project's own curl
broker and VM/scan adapters. It does not claim an original search algorithm,
HTML parser, antivirus engine or hypervisor.
