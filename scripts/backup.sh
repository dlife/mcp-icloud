#!/bin/sh
# Back-up van .env en het OAuth-volume naar een map op de NAS.
# Gebruik (in de projectmap, als root, bv. als geplande taak in DSM):
#   sh scripts/backup.sh /volume1/NASBackupSync/icloud-mcp-backup
# Houdt de laatste 14 back-ups. LET OP: bevat je app-wachtwoord en tokens; zet de map niet in een gedeelde share.
set -eu
DEST="${1:?Gebruik: backup.sh <doelmap>}"
KEEP=14
HERE="$(cd "$(dirname "$0")/.." && pwd)"
VOLUME="$(docker volume ls -q | grep 'icloud-mcp-data$' | head -n 1 || true)"
[ -n "$VOLUME" ] || { echo "Volume icloud-mcp-data niet gevonden" >&2; exit 1; }
[ -f "$HERE/.env" ] || { echo ".env niet gevonden in $HERE" >&2; exit 1; }

umask 077
mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cp "$HERE/.env" "$TMP/env"
docker run --rm -v "$VOLUME":/data:ro -v "$TMP":/out alpine tar czf /out/oauth-data.tgz -C /data .
tar czf "$DEST/icloud-mcp-$STAMP.tgz" -C "$TMP" env oauth-data.tgz
chmod 600 "$DEST/icloud-mcp-$STAMP.tgz"

# oude back-ups opruimen
ls -1t "$DEST"/icloud-mcp-*.tgz | tail -n +$((KEEP + 1)) | xargs -r rm -f
echo "Back-up klaar: $DEST/icloud-mcp-$STAMP.tgz"
