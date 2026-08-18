#!/usr/bin/env bash
# Fire an adaptation job.
#
# Usage: ./scripts/fire_job.sh [LABEL] [COURSE_ID] [AUDIENCE_PROFILE_ID]
#   LABEL                version label for the new refraction (default: "managers v1")
#   COURSE_ID            source course (default: flagship "AI at Work")
#   AUDIENCE_PROFILE_ID  refraction target (default: "managers" profile)
#
# All three may also be set via env vars of the same name. Requires uvicorn
# running. Examples:
#   ./scripts/fire_job.sh                       # flagship + managers, label "managers v1"
#   ./scripts/fire_job.sh "managers v2"         # same course/profile, new label
#   ./scripts/fire_job.sh "AEC v3" "$CID" "$PID"
set -euo pipefail

# Defaults: flagship "AI at Work" canonical course + "managers" audience profile
# (stable across ingest re-runs; see scripts/ingest_canonical.py).
LABEL="${1:-${LABEL:-managers v1}}"
COURSE_ID="${2:-${COURSE_ID:-3a3984e7-10ca-4e98-9abf-32b8259139f1}}"
AUDIENCE_PROFILE_ID="${3:-${AUDIENCE_PROFILE_ID:-9133a41b-705a-4de2-bf47-a8fe6e8b7b6b}}"

SUPABASE_URL="https://djurpvzvjbttwqccptdo.supabase.co"
PUBLISHABLE_KEY="sb_publishable_fNcigULYZ-c1_O2285k14Q_R5Wl3SSC"
API="http://localhost:8000"

echo "Course:   $COURSE_ID"
echo "Audience: $AUDIENCE_PROFILE_ID"
echo "Label:    $LABEL"

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
