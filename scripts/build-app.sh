#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="${1:-native}"
case "$ARCH" in native|universal) ;; *) echo 'Usage: scripts/build-app.sh [native|universal] [destination.app]' >&2; exit 2;; esac
command -v swiftc >/dev/null || { echo 'Install Xcode Command Line Tools first: xcode-select --install' >&2; exit 1; }
DESTINATION="${2:-$ROOT/build/Fauteuil.app}"
PARENT="$(dirname -- "$DESTINATION")"
mkdir -p "$PARENT"
PARENT="$(cd -- "$PARENT" && pwd)"
DESTINATION="$PARENT/$(basename -- "$DESTINATION")"
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
RESOURCES="$APP/Contents/Resources"
SOURCES=("$ROOT/app/macos/Cinema.swift" "$ROOT/app/macos/ApplicationInstallation.swift")
mkdir -p "$APP/Contents/MacOS" "$RESOURCES/downloader"
if [[ "$ARCH" == universal ]]; then
  SDK="$(xcrun --sdk macosx --show-sdk-path)"
  for target in arm64 x86_64; do
    swiftc -parse-as-library -O -sdk "$SDK" -target "$target-apple-macos15.0" -framework AppKit -framework WebKit "${SOURCES[@]}" -o "$WORK/Cinema-$target"
  done
  lipo -create "$WORK/Cinema-arm64" "$WORK/Cinema-x86_64" -output "$WORK/Cinema"
else
  swiftc -parse-as-library -O -target "$(uname -m)-apple-macos15.0" -framework AppKit -framework WebKit "${SOURCES[@]}" -o "$WORK/Cinema"
fi
cp "$WORK/Cinema" "$APP/Contents/MacOS/Cinema"
cp "$ROOT/app/macos/Info.plist" "$APP/Contents/Info.plist"
cp "$ROOT/scripts/install-runtime.sh" "$RESOURCES/install-runtime.sh"
cp "$ROOT/ytmp4.sh" "$ROOT/ytmp4-page.py" "$ROOT/ytmp4-stream.py" "$RESOURCES/downloader/"
(
  cd "$ROOT/app"
  while IFS= read -r -d '' source; do
    mkdir -p "$RESOURCES/$(dirname "$source")"
    cp "$source" "$RESOURCES/$source"
  done < <(find fauteuil -type f -name '*.py' -print0)
)
cp -R "$ROOT/app/web" "$RESOURCES/web"
cp "$ROOT/branding/Fauteuil.icns" "$RESOURCES/Fauteuil.icns"
chmod +x "$APP/Contents/MacOS/Cinema" "$RESOURCES/downloader/ytmp4.sh"
(
  cd "$RESOURCES"
  # Check the packaged entry points without starting services or writing bytecode.
  env -u PYTHONPATH python3 -B -c 'import fauteuil.__main__; import fauteuil.downloads.worker'
)
codesign --force --sign - "$APP"
codesign --verify --strict "$APP"
if [[ -e "$DESTINATION" ]]; then
  mv "$DESTINATION" "$PREVIOUS"
fi
mv "$APP" "$DESTINATION"
echo "Built $DESTINATION ($ARCH, macOS 15+)"
