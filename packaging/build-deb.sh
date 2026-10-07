#!/usr/bin/env bash
# Build dist/token-monitor_<version>_all.deb  (Ubuntu/Debian, GNOME).  Usage: ./packaging/build-deb.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VER="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$REPO/token_monitor/__init__.py")"
OUT="$REPO/dist"; ROOT="$(mktemp -d)"; trap 'rm -rf "$ROOT"' EXIT
LIB="$ROOT/usr/lib/token-monitor"; ID=dev.cividati.TokenMonitor
EXT="$ROOT/usr/share/gnome-shell/extensions/token-monitor@local"

install -d "$LIB" "$ROOT/usr/bin" "$ROOT/DEBIAN" "$EXT" "$ROOT/usr/share/applications" \
  "$ROOT/usr/share/icons/hicolor/scalable/apps"
cp -r "$REPO/token_monitor" "$REPO/bin" "$REPO/data" "$LIB/"
find "$LIB" -name __pycache__ -prune -exec rm -rf {} +
chmod 755 "$LIB/bin/token-monitor"
ln -s ../lib/token-monitor/bin/token-monitor "$ROOT/usr/bin/token-monitor"

cp "$REPO"/gnome-extension/* "$EXT/"
sed 's#^Exec=.*#Exec=token-monitor#' "$REPO/data/$ID.desktop" > "$ROOT/usr/share/applications/$ID.desktop"
cp "$REPO/data/icons/$ID.svg" "$ROOT/usr/share/icons/hicolor/scalable/apps/"

cat > "$ROOT/DEBIAN/control" <<CTL
Package: token-monitor
Version: $VER
Architecture: all
Maintainer: Token Monitor contributors <noreply@example.com>
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1
Recommends: gh
Section: utils
Priority: optional
Description: GitHub Copilot spend vs. monthly budget
 GTK4 app and GNOME top-bar indicator showing Copilot premium-request usage.
CTL
printf '#!/bin/sh\ngtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true\nupdate-desktop-database -q 2>/dev/null || true\n' > "$ROOT/DEBIAN/postinst"
chmod 755 "$ROOT/DEBIAN/postinst"

chmod 755 "$ROOT"
chmod -R go-w,a+rX "$ROOT/usr"     # umask-independent: dirs 755, files 644 (bin/token-monitor stays 755)
mkdir -p "$OUT"
dpkg-deb --root-owner-group --build "$ROOT" "$OUT/token-monitor_${VER}_all.deb" >/dev/null
echo "built $OUT/token-monitor_${VER}_all.deb"
