#!/bin/sh
# Synchroniseert iCloud Drive naar /data en herhaalt dat elk uur.
# Schrijft /data/.mirror-ok na elke geslaagde run; faalt de login (bv. na ~30 dagen), dan
# blijft dat bestand oud en wordt de container "unhealthy": voer dan `rclone reconnect` uit (zie README).
set -u
INTERVAL="${MIRROR_INTERVAL:-3600}"
while true; do
  echo "$(date -Iseconds) start synchronisatie"
  if rclone sync "${MIRROR_REMOTE}" /data \
      --exclude ".mirror-ok" \
      --max-delete "${MIRROR_MAX_DELETE:-200}" \
      --cache-dir /tmp --transfers 4 --retries 2 --log-level NOTICE; then
    touch /data/.mirror-ok
    echo "$(date -Iseconds) klaar"
  else
    echo "$(date -Iseconds) MISLUKT, inloggen opnieuw nodig? Zie README (rclone reconnect)"
  fi
  sleep "$INTERVAL"
done
