#!/bin/sh
# Installatiewizard / setup wizard: maakt .env aan en test je logins.
# Vereist alleen Docker. / Only needs Docker.
#   ./setup.sh            wizard
#   ./setup.sh --lang en  English
set -eu
cd "$(dirname "$0")"
docker build -q -t icloud-mcp-setup . >/dev/null
# .env komt in de huidige map, met jouw eigen gebruiker als eigenaar
docker run --rm -it --user "$(id -u):$(id -g)" -v "$PWD:/work" -w /work \
  --entrypoint icloud-mcp-setup icloud-mcp-setup --out /work/.env "$@"
