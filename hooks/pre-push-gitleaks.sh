#!/bin/sh
# Refuse a push that contains a secret. Call from .git/hooks/pre-push and pass its stdin through:
#   "$HOME/Projects/commit-reviewer/hooks/pre-push-gitleaks.sh" < /dev/stdin || exit 1
# Reads git's pre-push lines: <local ref> <local sha> <remote ref> <remote sha>
GITLEAKS="$(command -v gitleaks || echo "$HOME/.local/bin/gitleaks")"
if [ ! -x "$GITLEAKS" ]; then
  echo "gitleaks not installed (run commit-reviewer/deploy/install_gitleaks.sh). Push aborted." >&2
  exit 1
fi
ZERO=0000000000000000000000000000000000000000
status=0
while read -r local_ref local_sha remote_ref remote_sha; do
  [ "$local_sha" = "$ZERO" ] && continue            # deleting a branch
  if [ "$remote_sha" = "$ZERO" ]; then
    range="$local_sha --not --remotes"               # new branch: only commits no remote has
  else
    range="$remote_sha..$local_sha"
  fi
  echo "Scanning pushed commits for secrets..."
  if ! "$GITLEAKS" git --log-opts="$range" --redact --no-banner --exit-code 1 >&2; then
    echo "Possible secret in the commits being pushed. Push aborted." >&2
    echo "Remove it from history (amend/rebase), rotate it if real, or allowlist a false positive in .gitleaks.toml." >&2
    status=1
  fi
done
exit $status
