#!/usr/bin/env bash
# List every call in the keyed build that reads the seed or another source of randomness,
# one JSON object per line (harness/KeyedAudit). The unguarded ones must each have a reviewed
# reason in harness/keyed_audit_allowlist.json; tests/test_the_keyed_harness_has_no_unreviewed_randomness.py checks it.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(dirname "$here")
version=${1:?usage: audit_keyed.sh GAME_VERSION}
keyed=$repo/build/keyed/$version
[[ -f $keyed/Sts2Headless.dll ]] || { echo "no keyed build at $keyed; run harness/build_keyed.sh $version first" >&2; exit 1; }
dotnet run --project "$here/KeyedAudit" -c Release -- "$keyed/lib/sts2.dll" "$keyed/Sts2Headless.dll"
