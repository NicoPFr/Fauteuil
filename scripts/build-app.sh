#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="${1:-native}"
case "$ARCH" in native|universal) ;; *) echo 'Usage: scripts/build-app.sh [native|universal] [destination.app]' >&2; exit 2;; esac
command -v swiftc >/dev/null || { echo 'Install Xcode Command Line Tools first: xcode-select --install' >&2; exit 1; }
DESTINATION="${2:-$ROOT/build/Fauteuil.app}"
PARENT="$(dirname -- "$DESTINATION")"
mkdir -p "$PARENT"
WORK="$(mktemp -d "$PARENT/.fauteuil-build.XXXXXX")"
APP="$WORK/Fauteuil.app"
PREVIOUS="$WORK/previous.app"
cleanup() {
  if [[ -e "$PREVIOUS" && ! -e "$DESTINATION" ]]; then
    mv "$PREVIOUS" "$DESTINATION" || return
  fi
  python3 -c "import shutil,sys; shutil.rmtree(sys.argv[1])" "$WORK"
}
trap cleanup EXIT
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/static" "$APP/Contents/Resources/fauteuil"
if [[ "$ARCH" == universal ]]; then
  SDK="$(xcrun --sdk macosx --show-sdk-path)"
  for target in arm64 x86_64; do
    swiftc -parse-as-library -O -sdk "$SDK" -target "$target-apple-macos13.0" -framework AppKit -framework WebKit "$ROOT/app/Cinema.swift" -o "$WORK/Cinema-$target"
  done
  lipo -create "$WORK/Cinema-arm64" "$WORK/Cinema-x86_64" -output "$WORK/Cinema"
else
  swiftc -parse-as-library -O -target "$(uname -m)-apple-macos13.0" -framework AppKit -framework WebKit "$ROOT/app/Cinema.swift" -o "$WORK/Cinema"
fi
cp "$WORK/Cinema" "$APP/Contents/MacOS/Cinema"
cp "$ROOT/app/Info.plist" "$APP/Contents/Info.plist"
cp "$ROOT/app/app.py" "$ROOT/ytmp4.sh" "$ROOT/ytmp4-page.py" "$ROOT/ytmp4-stream.py" "$APP/Contents/Resources/"
cp "$ROOT/app/fauteuil/"*.py "$APP/Contents/Resources/fauteuil/"
cp "$ROOT/app/static/"* "$APP/Contents/Resources/static/"
cp "$ROOT/branding/Fauteuil.icns" "$APP/Contents/Resources/Fauteuil.icns"
chmod +x "$APP/Contents/MacOS/Cinema" "$APP/Contents/Resources/ytmp4.sh"
codesign --force --sign - "$APP"
codesign --verify --strict "$APP"
if [[ -e "$DESTINATION" ]]; then
  mv "$DESTINATION" "$PREVIOUS"
fi
mv "$APP" "$DESTINATION"
echo "Built $DESTINATION ($ARCH, macOS 13+)"
