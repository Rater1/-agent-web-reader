#!/bin/sh
set -eu
exec > /dev/ttyS0 2>&1
cd /mnt/fixtures
sha256sum -c input_checksums.txt
apk verify /mnt/fixtures/apks/*.apk
apk --no-network --repositories-file /dev/null add --force-non-repository /mnt/fixtures/apks/*.apk
python3 -c 'import sys,bs4; print("PARSER_RUNTIME",sys.version.split()[0],bs4.__version__)'
test "$(ls /sys/class/net)" = lo
timeout 15 python3 -B test_worker.py
timeout 15 python3 -O -B test_worker.py
timeout 30 python3 -B guest_batch.py /mnt/fixtures
echo TAVILY_GUEST_DONE
