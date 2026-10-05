#!/usr/bin/env bash
# Wire Ammeter into the desktop: the `ammeter` command, the app-grid entry, and the GNOME top-bar
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
UUID="ammeter@cividati"

if [[ "${1:-}" == "--undo" ]]; then
    rm -f "${BIN}/ammeter"
    rm -f "${APPS}/dev.cividati.Ammeter.desktop"
    rm -f "${HICOLOR}/scalable/apps/dev.cividati.Ammeter.svg"
    rm -rf "${EXT_ROOT}/${UUID}"
    echo "removed the ammeter command, desktop entry, icon and extension link"
    exit 0
fi

mkdir -p "${BIN}" "${APPS}" "${HICOLOR}/scalable/apps" "${EXT_ROOT}"
chmod +x "${REPO}/bin/ammeter" "${REPO}/scripts/fetch-icons.py" "${REPO}/tests/icon-check.py"

ln -sfn "${REPO}/bin/ammeter" "${BIN}/ammeter"
ln -sfn "${REPO}/data/dev.cividati.Ammeter.desktop" "${APPS}/dev.cividati.Ammeter.desktop"
ln -sfn "${REPO}/data/icons/dev.cividati.Ammeter.svg" "${HICOLOR}/scalable/apps/dev.cividati.Ammeter.svg"
gtk-update-icon-cache -f -t "${HICOLOR}" >/dev/null 2>&1 || true

rm -rf "${EXT_ROOT}/${UUID}"
ln -sfn "${REPO}/gnome-extension" "${EXT_ROOT}/${UUID}"

python3 "${REPO}/scripts/enable-extension.py"

echo
echo "installed. Available as:"
echo "  ammeter            native GTK4 app"
echo "  ammeter --text     plain snapshot"
echo "  ammeter --selftest checks"
echo
echo "The top-bar indicator needs one log out / log in to load (GNOME Shell cannot reload on Wayland)."
