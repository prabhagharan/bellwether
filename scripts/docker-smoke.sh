#!/usr/bin/env bash
# Live smoke for the composed stack. Run on a machine with Docker after `docker compose up -d --build`.
set -euo pipefail
API=http://localhost:8000
WEB=http://localhost:3000
pass=0; fail=0
chk() { if [ "$1" = "$2" ]; then echo "  PASS: $3"; pass=$((pass+1)); else echo "  FAIL: $3 (got $1 want $2)"; fail=$((fail+1)); fi; }

echo "== containers up =="
# `ps -a` so the one-shot migrator (which exits after `alembic upgrade head`) is visible;
# without -a it's omitted once finished and would misreport as 'missing'.
for svc in db migrator api detect extract resolve measure discovery alert frontend; do
  state=$(docker compose ps -a --format '{{.Service}} {{.State}}' | awk -v s="$svc" '$1==s{print $2}')
  # migrator is one-shot: 'exited' (code 0) is success; the long-running services should be 'running'.
  echo "  $svc: ${state:-missing}"
done

echo "== api serves openapi =="
chk "$(curl -s -o /dev/null -w '%{http_code}' $API/openapi.json)" "200" "GET $API/openapi.json"
echo "== frontend serves =="
chk "$(curl -s -o /dev/null -w '%{http_code}' $WEB/)" "200" "GET $WEB/"
echo "== CORS allows the frontend origin =="
acao=$(curl -s -D - -o /dev/null -H "Origin: http://localhost:3000" $API/leaderboard | tr -d '\r' | awk 'tolower($1)=="access-control-allow-origin:"{print $2}')
chk "$acao" "http://localhost:3000" "CORS allow-origin"
echo "== login + a write round-trips =="
TOK=$(curl -s -X POST $API/auth/token -H "Content-Type: application/x-www-form-urlencoded" \
      --data-urlencode "username=${ADMIN_USERNAME:-admin}" --data-urlencode "password=${ADMIN_PASSWORD:?set ADMIN_PASSWORD}" \
      | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))")
[ -n "$TOK" ] && { echo "  PASS: login"; pass=$((pass+1)); } || { echo "  FAIL: login"; fail=$((fail+1)); }
if [ -n "$TOK" ]; then
  RID=$(curl -s -X POST $API/alert_rules -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
        -d '{"name":"docker-smoke","condition":{"min_confidence":0.7},"webhook_url":null,"enabled":true}' \
        | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))")
  [ -n "$RID" ] && { echo "  PASS: create rule"; pass=$((pass+1)); curl -s -o /dev/null -X DELETE $API/alert_rules/$RID -H "Authorization: Bearer $TOK"; } || { echo "  FAIL: create rule"; fail=$((fail+1)); }
fi
echo ""; echo "== $pass passed, $fail failed =="
[ "$fail" = 0 ]
