#!/bin/bash
# Copy build/site to a Linux server and serve it with a systemd user unit (deploy/serve.py: gzip + ETag).
# Usage: HOST=user@server DIR=playing_architect_site PORT=8780 deploy/deploy.sh   (run `make site` first)
# Put a TLS reverse proxy in front (Caddy, nginx, or `tailscale funnel`); this script does not touch it.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${HOST:?set HOST=user@server}"
DIR=${DIR:-playing_architect_site}
PORT=${PORT:-8780}
UNIT=playing-architect.service
test -f build/site/index.html && test -f build/site/build/house.glb

ssh -o BatchMode=yes "$HOST" "mkdir -p ~/$DIR ~/.config/systemd/user"
rsync -az --delete build/site/ "$HOST:$DIR/"
scp -q deploy/serve.py "$HOST:$DIR/.serve.py"
sed -e "s|@DIR@|$DIR|g" -e "s|@PORT@|$PORT|g" deploy/playing-architect.service.sample | ssh "$HOST" "cat > ~/.config/systemd/user/$UNIT"
ssh "$HOST" "systemctl --user daemon-reload && systemctl --user enable --now $UNIT && systemctl --user restart $UNIT"
sleep 2
for u in / /viewer/ /viewer/main.js /build/house.glb; do
  code=$(ssh "$HOST" "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT$u")
  echo "$code $u"; [ "$code" = 200 ] || { echo "deploy failed at $u"; exit 1; }
done
echo "serving on $HOST 127.0.0.1:$PORT"
