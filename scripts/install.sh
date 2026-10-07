#!/usr/bin/env bash
# Wire Token Monitor into the desktop: the `token-monitor` command, the app-grid entry, and the GNOME top-bar
# indicator. Symlinks are used on purpose, so edits in this checkout take effect with no reinstall.
#
#   ./scripts/install.sh          install / refresh the links
#   ./scripts/install.sh --undo   remove everything this script created
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${HOME}/.local/bin"
APPS="${HOME}/.local/share/applications"
HICOLOR="${HOME}/.local/share/icons/hicolor"
EXT_ROOT="${HOME}/.local/share/gnome-shell/extensions"
UUID="token-monitor@local"

if [[ "${1:-}" == "--undo" ]]; then
    rm -f "${BIN}/token-monitor"
    rm -f "${APPS}/dev.cividati.TokenMonitor.desktop"
    rm -f "${HICOLOR}/scalable/apps/dev.cividati.TokenMonitor.svg"
    rm -rf "${EXT_ROOT}/${UUID}"
    echo "removed the token-monitor command, desktop entry, icon and extension link"
    exit 0
fi

mkdir -p "${BIN}" "${APPS}" "${HICOLOR}/scalable/apps" "${EXT_ROOT}"
chmod +x "${REPO}/bin/token-monitor"

ln -sfn "${REPO}/bin/token-monitor" "${BIN}/token-monitor"
ln -sfn "${REPO}/data/dev.cividati.TokenMonitor.desktop" "${APPS}/dev.cividati.TokenMonitor.desktop"
ln -sfn "${REPO}/data/icons/dev.cividati.TokenMonitor.svg" "${HICOLOR}/scalable/apps/dev.cividati.TokenMonitor.svg"
gtk-update-icon-cache -f -t "${HICOLOR}" >/dev/null 2>&1 || true

rm -rf "${EXT_ROOT}/${UUID}"
ln -sfn "${REPO}/gnome-extension" "${EXT_ROOT}/${UUID}"

python3 "${REPO}/scripts/enable-extension.py"

echo
echo "installed. Available as:"
echo "  token-monitor            native GTK4 app"
echo "  token-monitor --text     plain snapshot"
echo "  token-monitor --selftest checks"
echo
echo "The top-bar indicator needs one log out / log in to load (GNOME Shell cannot reload on Wayland)."
