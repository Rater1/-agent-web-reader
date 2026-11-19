#!/bin/sh
set -eu
cd /mnt/fixtures
sha256sum -c checksums.txt
test "$(ls /sys/class/net)" = lo
test ! -e /tmp/capture
test ! -e /tmp/shot_dirty
echo WARM_TASK_CLEAN
touch /tmp/shot_dirty
echo CAPTURE_BEGIN
su browser -s /bin/sh -c 'timeout 75 node /mnt/fixtures/screenshot_guest.mjs'
echo SCREENSHOT_DONE
