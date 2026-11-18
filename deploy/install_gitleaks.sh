#!/usr/bin/env bash
# Install a pinned gitleaks into $1 (default ~/.local/bin), verifying the release checksum.
set -euo pipefail
VERSION=8.30.1
DEST="${1:-$HOME/.local/bin}"
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) PLATFORM=darwin_arm64 ;;
  Darwin-x86_64) PLATFORM=darwin_x64 ;;
  Linux-x86_64) PLATFORM=linux_x64 ;;
  Linux-aarch64) PLATFORM=linux_arm64 ;;
  *) echo "unsupported platform" >&2; exit 1 ;;
esac
TARBALL="gitleaks_${VERSION}_${PLATFORM}.tar.gz"
BASE="https://github.com/gitleaks/gitleaks/releases/download/v${VERSION}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/$TARBALL" "$BASE/$TARBALL"
curl -fsSL -o "$TMP/checksums.txt" "$BASE/gitleaks_${VERSION}_checksums.txt"
expected="$(grep " $TARBALL\$" "$TMP/checksums.txt" | awk '{print $1}')"
actual="$( (shasum -a 256 "$TMP/$TARBALL" 2>/dev/null || sha256sum "$TMP/$TARBALL") | awk '{print $1}')"
[ -n "$expected" ] && [ "$expected" = "$actual" ] || { echo "gitleaks checksum mismatch" >&2; exit 1; }
mkdir -p "$DEST"
tar -xzf "$TMP/$TARBALL" -C "$TMP" gitleaks
install -m 0755 "$TMP/gitleaks" "$DEST/gitleaks"
"$DEST/gitleaks" version
