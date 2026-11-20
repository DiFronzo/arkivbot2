#!/bin/bash
# Rebuild the Toolforge virtualenv that run.sh uses, from pinned inputs.
#
# Usage (from the tool home on a Toolforge bastion):
#   toolforge jobs run bootstrap-venv --command "./bootstrap-venv.sh" --image python3.13 --wait
#
# A venv must be created *inside* the execution image, never on the bastion host,
# otherwise the interpreter and the C extensions won't match.
#
# Inputs, all committed so that production is reproducible:
#   PYWIKIBOT_VERSION  the pywikibot release tag the checkout is pinned to
#   requirements.txt   hashed runtime requirements (regenerate with uv)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || exit 1

VENV="$HERE/ENV"
PYWIKI="${PYWIKI:-$HERE/pywikibot}"
REF="$(tr -d '[:space:]' < PYWIKIBOT_VERSION)"

if [[ -z "$REF" ]]; then
    echo "❌ PYWIKIBOT_VERSION is empty" >&2
    exit 1
fi
if [[ ! -f requirements.txt ]]; then
    echo "❌ requirements.txt is missing (uv export --frozen --no-dev --no-emit-project)" >&2
    exit 1
fi

echo "🐍 $(python3 -V), pywikibot $REF"

# Serialise against run.sh so a rebuild cannot delete a venv that is in use.
if command -v flock >/dev/null 2>&1; then
    exec 9>"$HERE/.arkivbot.lock"
    flock -w 600 9 || { echo "❌ could not lock $HERE/.arkivbot.lock" >&2; exit 1; }
fi

rm -rf "$VENV"
python3 -m venv "$VENV"
# shellcheck source=/dev/null  # created just above, at runtime
. "$VENV/bin/activate"

# Installer tooling only; the runtime itself comes from the hashed lock file.
python -m pip install -U pip wheel

pip install --require-hashes -r requirements.txt

# archivebot.py ships with the pywikibot git checkout but *not* with the PyPI
# wheel, and the i18n submodule holds the edit summaries. run.sh puts this
# checkout on PYTHONPATH; it is not installed into the venv on purpose, so that
# the wheel and the checkout cannot drift apart.
if [[ ! -d "$PYWIKI/.git" ]]; then
    git clone --depth 1 --branch "$REF" --recurse-submodules --shallow-submodules \
        https://github.com/wikimedia/pywikibot.git "$PYWIKI"
else
    git -C "$PYWIKI" fetch --depth 1 origin "refs/tags/$REF:refs/tags/$REF"
    git -C "$PYWIKI" checkout --detach "$REF"
    git -C "$PYWIKI" submodule update --init --recursive --depth 1
fi

FOUND="$(git -C "$PYWIKI" describe --tags --exact-match 2>/dev/null || echo 'unknown')"
if [[ "$FOUND" != "$REF" ]]; then
    echo "❌ checkout is at '$FOUND', expected '$REF'" >&2
    exit 1
fi
if [[ ! -f "$PYWIKI/scripts/archivebot.py" || ! -d "$PYWIKI/scripts/i18n/archivebot" ]]; then
    echo "❌ checkout is missing archivebot.py or the i18n submodule" >&2
    exit 1
fi

echo "🔍 verifying versions"
PYTHONPATH="$PYWIKI" python - "$REF" <<'PY'
import sys

import pywikibot

expected = sys.argv[1]
if pywikibot.__version__ != expected:
    raise SystemExit(f'❌ installed {pywikibot.__version__}, expected {expected}')
print(f'✅ pywikibot {pywikibot.__version__} (wheel + checkout)')
PY

echo "✅ Done. $(python -V) venv ready in $VENV, checkout at $REF"
