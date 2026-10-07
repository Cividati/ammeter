# Releasing

Releases are cut by hand from `master`. Replace `X.Y.Z` with the new version.

1. **Version.** Set `__version__ = "X.Y.Z"` in `token_monitor/__init__.py` (the `.deb` builder and the
   app read it from there). Keep `version` in `gnome-extension/metadata.json` as is unless the extension
   changed (it is GNOME's own integer counter).
2. **Changelog.** In `CHANGELOG.md` move the *Unreleased* notes under a new `## [X.Y.Z] - YYYY-MM-DD`
   heading and update the link at the bottom.
3. **Docs.** Make sure README, `docs/HOW-IT-WORKS.md`, `docs/WEB.md`, the web "How it's calculated" /
   "How to set up" pages and `.env.example` still match the code.
4. **Checks.**
   ```sh
   python3 bin/token-monitor --selftest
   gjs -m tests/test-format.js
   node --check token_monitor/web/app.js
   ```
5. **Build the package.**
   ```sh
   ./packaging/build-deb.sh                      # dist/token-monitor_X.Y.Z_all.deb
   dpkg-deb -I dist/token-monitor_X.Y.Z_all.deb  # version and dependencies
   dpkg-deb -c dist/token-monitor_X.Y.Z_all.deb  # contents: web assets, data, bin; no .env or history
   ```
6. **Commit and tag.**
   ```sh
   git commit -am "Release vX.Y.Z"
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin master vX.Y.Z
   ```
7. **Publish.**
   ```sh
   gh release create vX.Y.Z dist/token-monitor_X.Y.Z_all.deb \
     --title "vX.Y.Z" --notes-file <(sed -n '/^## \[X.Y.Z\]/,/^## \[/p' CHANGELOG.md | sed '$d')
   ```
   (or paste the changelog section into `--notes`). `dist/` is gitignored; the `.deb` is only attached to
   the release.
