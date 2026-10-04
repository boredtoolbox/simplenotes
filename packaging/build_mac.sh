#!/bin/bash
# Build dist/SimpleNotes.app and dist/SimpleNotes.dmg.
#
# Uses a throwaway virtualenv in build/ for PyInstaller (build tooling only;
# running from source still needs no venv). The app is built for the
# architecture of the Python used (arm64 with Homebrew Python on Apple Silicon).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
DIST="$ROOT/dist"
PYTHON="${PYTHON:-python3}"

mkdir -p "$BUILD"
if [ ! -x "$BUILD/venv/bin/python" ]; then
    "$PYTHON" -m venv "$BUILD/venv"
fi
PIP_USER=0 "$BUILD/venv/bin/python" -m pip install --quiet --upgrade pip pyinstaller PyQt6 Pygments Pillow

# App icon: cut the artwork out of simplenotesiconimage.jpg (removing any baked-in
# checkerboard background), square it, and keep a copy for the app's runtime icon.
SRC="$ROOT/simplenotesicon.png"
"$BUILD/venv/bin/python" "$ROOT/packaging/make_icon.py" "$ROOT/simplenotesiconimage.jpg" "$SRC"
ICONSET="$BUILD/SimpleNotes.iconset"
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
    sips -z $size $size "$SRC" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z $double $double "$SRC" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$BUILD/SimpleNotes.icns"

rm -rf "$DIST/SimpleNotes.app" "$DIST/SimpleNotes" "$DIST/SimpleNotes.dmg"
"$BUILD/venv/bin/pyinstaller" --noconfirm --clean \
    --workpath "$BUILD/pyinstaller" --distpath "$DIST" \
    "$ROOT/packaging/SimpleNotes.spec"
rm -rf "$DIST/SimpleNotes"  # the intermediate folder; the .app is self-contained

# Disk image with an /Applications shortcut for drag-to-install.
STAGE="$BUILD/dmg"
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R "$DIST/SimpleNotes.app" "$STAGE/"
cp "$ROOT/LICENSE" "$STAGE/LICENSE.txt"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Simple Notes" -srcfolder "$STAGE" -ov -format UDZO "$DIST/SimpleNotes.dmg" >/dev/null

echo "Built:"
du -sh "$DIST/SimpleNotes.app" "$DIST/SimpleNotes.dmg"
