#!/usr/bin/env bash
# Build the patched Sts2Headless for one game version, into
# build/harness-src/<version>/src/Sts2Headless/bin/<version>/Release/net9.0/ (it finds lib/ and
# localization_eng*/ by walking up from there).
# Copies the pinned vendor/spire-mentor harness source to build/harness-src/<version>/,
# applies harness/patches/*.patch in name order (refusing any hunk that does not apply exactly),
# and builds it with the game DLLs of that version. build_keyed.sh keys this build.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(dirname "$here")
version=${1:?usage: build_harness.sh GAME_VERSION}
pycli=$repo/vendor/spire-mentor/sts2-pycli
[[ -f $pycli/lib/$version/sts2.dll ]] || { echo "no game DLLs at $pycli/lib/$version" >&2; exit 1; }
src=$repo/build/harness-src/$version
rm -rf "$src"
mkdir -p "$src/src"
cp -r "$pycli/src/Sts2Headless" "$pycli/src/GodotStubs" "$src/src/"
rm -rf "$src"/src/*/bin "$src"/src/*/obj
ln -s "$pycli/lib" "$src/lib"
for loc in "$pycli"/localization_eng*; do ln -s "$loc" "$src/$(basename "$loc")"; done
for p in "$here"/patches/*.patch; do
  [[ -e $p ]] || continue
  patch -d "$src" -p1 --forward --fuzz=0 --no-backup-if-mismatch -s < "$p" \
    || { echo "patch $(basename "$p") does not apply to the pinned harness" >&2; exit 1; }
done
dotnet build "$src/src/Sts2Headless/Sts2Headless.csproj" -c Release -p:GameVersion="$version" --nologo -v q
