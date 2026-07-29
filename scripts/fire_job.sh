#!/usr/bin/env bash
# Fire an adaptation job. Usage: ./scripts/fire_job.sh "AEC v3"
# Requires: uvicorn running; jq not required.
set -euo pipefail

LABEL="${1:?usage: fire_job.sh \"<version label>\"}"
SUPABASE_URL="https://djurpvzvjbttwqccptdo.supabase.co"
PUBLISHABLE_KEY="sb_publishable_fNcigULYZ-c1_O2285k14Q_R5Wl3SSC"
COURSE_ID="cd0fac68-0078-408c-b77e-326cbddae3a9"
AUDIENCE_PROFILE_ID="640d3ede-864a-4c51-b956-56cfa3be3272"
API="http://localhost:8000"

read -p "Author email: " EMAIL
read -s -p "Password: " PASSWORD; echo

TOKEN=$(curl -s -X POST "$SUPABASE_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $PUBLISHABLE_KEY" -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s -X POST "$API/adaptation/jobs" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"course_id\": \"$COURSE_ID\", \"audience_profile_id\": \"$AUDIENCE_PROFILE_ID\", \"label\": \"$LABEL\"}" \
  | python3 -m json.tool