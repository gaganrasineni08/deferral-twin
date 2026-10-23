#!/bin/bash
# Build DeferralTwin.app and DeferralTwin.dmg (HANDOFF.md sections 9.1 to 9.3). Run on an Apple Silicon Mac:
#
#   ./packaging/build_app.sh            # -> dist/DeferralTwin.app and dist/DeferralTwin.dmg
#
# The .app is the small bootstrapper only (about 80 MB): a standalone Python 3.12 with
# pywebview, the uv binary, and this project's code. Models and the three environments
# are downloaded by the app's own setup screen on first launch, into
# ~/Library/Application Support/DeferralTwin/. Nothing is ever written inside the bundle.
#
# Needs: curl, codesign, hdiutil (all part of macOS). Internet, once.
# Signing is ad-hoc (no Apple Developer account, section 9.1).
set -euo pipefail

UV_VERSION="0.11.32"
PY_VERSION="3.12"
VERSION="1.0.0"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
WORK="$DIST/.work"
CACHE="$DIST/.cache"    # uv and the standalone Python, kept between builds so a rebuild needs no network
APP="$DIST/DeferralTwin.app"
C="$APP/Contents"
R="$C/Resources"

[ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ] || { echo "Build this on an Apple Silicon Mac."; exit 1; }

rm -rf "$APP" "$DIST/DeferralTwin.dmg" "$WORK"
mkdir -p "$WORK" "$CACHE" "$C/MacOS" "$R/bin"

echo "==> uv $UV_VERSION"
UV_TGZ="$CACHE/uv-$UV_VERSION-aarch64-apple-darwin.tar.gz"
if ! tar -tzf "$UV_TGZ" >/dev/null 2>&1; then
  curl -fL --retry 5 --retry-all-errors --connect-timeout 20 --speed-limit 1000 --speed-time 60 -C - \
    "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-aarch64-apple-darwin.tar.gz" -o "$UV_TGZ.part"
  mv "$UV_TGZ.part" "$UV_TGZ"
fi
tar -xzf "$UV_TGZ" -C "$WORK"
cp "$WORK/uv-aarch64-apple-darwin/uv" "$R/bin/uv"
chmod +x "$R/bin/uv"
UV="$R/bin/uv"

echo "==> standalone Python $PY_VERSION (python-build-standalone, via uv)"
UV_PYTHON_INSTALL_DIR="$CACHE/pythons" "$UV" python install "$PY_VERSION" --no-bin
PYDIR="$(find "$CACHE/pythons" -maxdepth 1 -type d -name "cpython-$PY_VERSION*-macos-aarch64-none" | head -1)"
[ -n "$PYDIR" ] || { echo "The standalone Python wasn't found."; exit 1; }
cp -R "$PYDIR" "$R/python"
PY="$R/python/bin/python3"
rm -f "$R/python/lib/python$PY_VERSION/EXTERNALLY-MANAGED"

echo "==> pywebview (packaging/bootstrap.lock)"
"$UV" pip install --python "$PY" --system --break-system-packages --no-deps -r "$ROOT/packaging/bootstrap.lock"
"$PY" -c "import webview, objc, WebKit; print('pywebview', webview.__version__ if hasattr(webview, '__version__') else 'ok')"
# trim what the launcher never uses
rm -rf "$R/python/lib/python$PY_VERSION/test" "$R/python/lib/python$PY_VERSION/idlelib" \
       "$R/python/lib/python$PY_VERSION/tkinter" "$R/python/lib/python$PY_VERSION/turtledemo" \
       "$R/python/share" "$R/python/include"
find "$R/python" -name "__pycache__" -type d -prune -exec rm -rf {} +

echo "==> the twin's code"
mkdir -p "$R/twin"
for d in app bootstrap configs src; do
  rsync -a --exclude "__pycache__" --exclude "*.pyc" "$ROOT/$d" "$R/twin/"
done
mkdir -p "$R/twin/docs" "$R/twin/data"
rsync -a "$ROOT/docs/figures" "$R/twin/docs/"
[ -d "$ROOT/data/consent" ] && rsync -a "$ROOT/data/consent" "$R/twin/data/"
cp "$ROOT/HANDOFF.md" "$ROOT/DESKTOP.md" "$R/twin/" 2>/dev/null || true
# compile once now, so the launcher (run with -B) never needs to write
"$PY" -m compileall -q "$R/twin/bootstrap" "$R/python/lib/python$PY_VERSION" >/dev/null || true

echo "==> launcher stub and Info.plist"
cat > "$C/MacOS/DeferralTwin" <<'EOF'
#!/bin/bash
# DeferralTwin.app: start the bootstrapper on the bundled Python. -I ignores any Python the
# user has installed; -B never writes .pyc files into the bundle.
R="$(cd "$(dirname "$0")/../Resources" && pwd)"
export PYTHONNOUSERSITE=1
mkdir -p "$HOME/Library/Logs"
exec "$R/python/bin/python3" -I -B "$R/twin/bootstrap/launcher.py" "$@" 2>>"$HOME/Library/Logs/DeferralTwin.log"
EOF
chmod +x "$C/MacOS/DeferralTwin"

cat > "$C/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Deferral Twin</string>
  <key>CFBundleDisplayName</key><string>Deferral Twin</string>
  <key>CFBundleIdentifier</key><string>edu.deferraltwin.app</string>
  <key>CFBundleExecutable</key><string>DeferralTwin</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleIconFile</key><string>DeferralTwin</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSArchitecturePriority</key><array><string>arm64</string></array>
  <key>LSApplicationCategoryType</key><string>public.app-category.education</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>Deferral Twin records you reading a short paragraph so your twin can speak in your voice. The recording stays on this Mac.</string>
  <key>NSCameraUsageDescription</key>
  <string>Deferral Twin takes one photo of your face so your twin's avatar can be animated. The photo stays on this Mac.</string>
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
EOF

echo "==> icon"
if [ -f "$ROOT/packaging/DeferralTwin.icns" ]; then
  cp "$ROOT/packaging/DeferralTwin.icns" "$R/DeferralTwin.icns"
else
  "$PY" "$ROOT/packaging/make_icon.py" "$WORK/icon.iconset" && iconutil -c icns "$WORK/icon.iconset" -o "$R/DeferralTwin.icns" \
    || echo "   (no icon: the app uses the generic one)"
fi

echo "==> ad-hoc signature"
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP" && echo "   signature ok"

echo "==> disk image"
STAGE="$WORK/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp "$ROOT/packaging/README.md" "$STAGE/README.md"
[ -f "$ROOT/packaging/gatekeeper.png" ] && cp "$ROOT/packaging/gatekeeper.png" "$STAGE/"
hdiutil create -volname "Deferral Twin" -srcfolder "$STAGE" -ov -format UDZO "$DIST/DeferralTwin.dmg" >/dev/null
rm -rf "$WORK"

du -sh "$APP" "$DIST/DeferralTwin.dmg"
echo "Done. Test it the honest way (DESKTOP.md, 'Clean-account test'): copy the .dmg to a fresh macOS user and open it there."
