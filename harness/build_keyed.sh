#!/usr/bin/env bash
# Build the keyed harness: build/keyed/<game version>/ (the gateway's worker), from the
# vendored Sts2Headless build and its game DLLs. Pass --plant-unkeyed-draw to build the
# test-only harness at build/keyed-plant/<game version>/ instead, or --trace-streams to build
# tools/fuzz_keyed.py's harness at build/keyed-trace/<game version>/, which logs each stream it opens.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(dirname "$here")
version=${1:?usage: build_keyed.sh GAME_VERSION [--plant-unkeyed-draw | --trace-streams]}
plant=${2:-}
pycli=$repo/vendor/spire-mentor/sts2-pycli
bin=$pycli/src/Sts2Headless/bin/$version/Release/net9.0
[[ -f $bin/Sts2Headless.dll ]] || { echo "no harness build at $bin; build Sts2Headless for $version first" >&2; exit 1; }
[[ -f $pycli/lib/$version/sts2.dll ]] || { echo "no game DLLs at $pycli/lib/$version" >&2; exit 1; }
out=$repo/build/keyed/$version
case $plant in
  "") ;;
  --plant-unkeyed-draw) out=$repo/build/keyed-plant/$version ;;
  --trace-streams) out=$repo/build/keyed-trace/$version ;;
  *) echo "unknown option $plant" >&2; exit 2 ;;
esac
dotnet build "$here/KeyedRng" -c Release -o "$repo/build/keyedrng" --nologo -v q
dotnet run --project "$here/KeyedPatcher" -c Release -- \
  --bin "$bin" --lib "$pycli/lib/$version" --keyedrng "$repo/build/keyedrng/KeyedRng.dll" \
  --localization "$pycli/localization_eng" --out "$out" $plant
