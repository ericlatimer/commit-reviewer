#!/bin/sh
# Called from deploy.sh before anything deploys. Refuses while the commit reviewer has an open
# severity:high issue. Override: REVIEWER_OVERRIDE="reason" ./deploy.sh  (the reason is logged).
# Needs REVIEWER_GITHUB_TOKEN (fine-grained, read-only Issues on the repo). Without it: warns, allows.
REPOS="${REVIEWER_REPOS:-ericlatimer/whatsNext ericlatimer/whats-next-cloud}"
LOG="${REVIEWER_OVERRIDE_LOG:-$HOME/.commit-reviewer-overrides.log}"
if [ -z "$REVIEWER_GITHUB_TOKEN" ]; then
  echo "⚠️  deploy gate: REVIEWER_GITHUB_TOKEN not set, open high findings NOT checked." >&2
  exit 0
fi
open=""
for repo in $REPOS; do
  out="$(curl -fsS -H "Authorization: Bearer $REVIEWER_GITHUB_TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$repo/issues?state=open&labels=severity:high&per_page=50")" || {
    echo "⚠️  deploy gate: couldn't read $repo issues; not checked." >&2; continue; }
  found="$(printf '%s' "$out" | python3 -c 'import json,sys
for i in json.load(sys.stdin): print(f"  #{i[\"number\"]} {i[\"title\"]}\n     {i[\"html_url\"]}")')"
  [ -n "$found" ] && open="$open$found
"
done
[ -z "$open" ] && { echo "deploy gate: no open high findings."; exit 0; }
echo "🔴 Open high-severity reviewer findings:" >&2
printf '%s' "$open" >&2
if [ -n "$REVIEWER_OVERRIDE" ]; then
  echo "$(date -u +%FT%TZ) override: $REVIEWER_OVERRIDE" >> "$LOG"
  echo "Overridden (logged to $LOG): $REVIEWER_OVERRIDE" >&2
  exit 0
fi
echo "Deploy refused. Fix and close them, close as not planned if false alarms, or set REVIEWER_OVERRIDE=\"reason\"." >&2
exit 1
