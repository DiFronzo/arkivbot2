#!/bin/bash
# Local test suite. Run before every deploy.
#
# Usage:
#   ./test-local.sh              # offline: syntax, pins, contract tests, status page, linters
#   ./test-local.sh --live       # additionally a read-only -simulate run on no.wikipedia
#   SKIP_LINT=1 ./test-local.sh  # skip the shellcheck/ruff/mypy gate (no network needed)
#
# Never reads .env: pywikibot loads it itself, and nothing here prints secrets.
# Never touches public_html/out or public_html/status: the serving test uses a
# temporary copy of the directory.

set -euo pipefail

cd "$(dirname "$0")" || exit 1

LIVE=0
for arg in "$@"; do
    case "$arg" in
        --live) LIVE=1 ;;
        -h | --help)
            sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "unknown argument: $arg (use --live)" >&2
            exit 2
            ;;
    esac
done

REF="$(tr -d '[:space:]' < PYWIKIBOT_VERSION)"
PYWIKI="$PWD/pywikibot"
export PYTHONPATH="$PYWIKI:$PYWIKI/scripts"

step() { printf '\n==> %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

step "nothing secret or native-dropping is tracked"
bash tests/tracked-files.test.sh || fail "secret or droppings in the index"

step "shell syntax"
for script in run.sh bootstrap-venv.sh test-local.sh; do
    bash -n "$script" || fail "bash -n $script"
done
echo "ok"

step "uv sync (pyproject.toml, python 3.13)"
uv sync --quiet
uv python find 3.13

step "pywikibot pinned to $REF"
if [[ ! -d "$PYWIKI/.git" ]]; then
    git clone --depth 1 --branch "$REF" --recurse-submodules --shallow-submodules \
        https://github.com/wikimedia/pywikibot.git "$PYWIKI"
fi
FOUND="$(git -C "$PYWIKI" describe --tags --exact-match 2>/dev/null || echo unknown)"
if [[ "$FOUND" != "$REF" ]]; then
    echo "checkout is at '$FOUND', switching to '$REF'"
    git -C "$PYWIKI" fetch --depth 1 origin "refs/tags/$REF:refs/tags/$REF"
    git -C "$PYWIKI" checkout --detach "$REF"
    git -C "$PYWIKI" submodule update --init --recursive --depth 1
fi
test -f "$PYWIKI/scripts/archivebot.py" || fail "archivebot.py missing from checkout"
test -d "$PYWIKI/scripts/i18n/archivebot" || fail "i18n submodule missing"
CHECKOUT_VERSION="$(uv run --quiet python -c 'import pywikibot; print(pywikibot.__version__)')"
[[ "$CHECKOUT_VERSION" == "$REF" ]] || fail "checkout reports $CHECKOUT_VERSION, expected $REF"
WHEEL_VERSION="$(uv run --quiet python -c 'import importlib.metadata as m; print(m.version("pywikibot"))')"
[[ "$WHEEL_VERSION" == "$REF" ]] || fail "installed pywikibot is $WHEEL_VERSION, expected $REF"
echo "ok: checkout and wheel both $REF"

# archivebot needs a user-config.py; use a throwaway read-only one when none exists
if [[ ! -f user-config.py && ! -f "$PYWIKI/user-config.py" && ! -f "$HOME/.pywikibot/user-config.py" ]]; then
    PYWIKIBOT_DIR="$(mktemp -d)"
    export PYWIKIBOT_DIR
    cat > "$PYWIKIBOT_DIR/user-config.py" <<'EOF'
family = 'wikipedia'
mylang = 'no'
usernames['wikipedia']['no'] = 'IngeniousBot'
EOF
    echo "==> no user-config.py found, using throwaway one in $PYWIKIBOT_DIR"
fi

step "user-config.py.example loads without pywikibot warnings"
EXAMPLE_CFG="$(mktemp -d)"
cp user-config.py.example "$EXAMPLE_CFG/user-config.py"
PYWIKIBOT_DIR="$EXAMPLE_CFG" PYTHONWARNINGS=error::UserWarning \
    uv run --quiet python -c "
import pywikibot
assert pywikibot.config.usernames['wikipedia']['no'] == 'IngeniousBot'
print('ok: no unknown-variable warnings')
" || fail "user-config.py.example triggers a pywikibot config warning"
rm -f "$EXAMPLE_CFG/user-config.py"
rmdir "$EXAMPLE_CFG"

step "configured account"
uv run --quiet python - <<'PY'
from pywikibot import config

for family, by_language in sorted(config.usernames.items()):
    for code, username in sorted(by_language.items()):
        print(f"{family}:{code} -> {username}")
sites = ", ".join(sorted(config.authenticate)) or "none (interactive login / bot password)"
print("OAuth:", sites)
PY

step "run.sh refuses an unpinned checkout"
set +e
PYWIKI="$(mktemp -d)" VENV="$PWD/.venv" ./run.sh --simulate >/dev/null 2>&1
rc=$?
set -e
[[ $rc -eq 1 ]] || fail "run.sh exited $rc for a non-pinned checkout, expected 1"
echo "ok"

step "a second concurrent run backs off"
if command -v flock >/dev/null 2>&1; then
    exec 8>"$PWD/.arkivbot.lock"
    flock -n 8
    set +e
    LOCK_OUT="$(PYTHON=/usr/bin/false PYWIKI="$PYWIKI" VENV="$PWD/.venv" ./run.sh --simulate 2>&1)"
    lock_rc=$?
    set -e
    flock -u 8
    exec 8>&-
    [[ $lock_rc -eq 0 ]] || fail "a locked-out run exited $lock_rc, expected 0"
    grep -q 'another run is in progress' <<<"$LOCK_OUT" || fail "locked-out run did not say why"
    echo "ok: exits 0 without doing anything"
else
    echo "skipped: flock not installed (macOS); CI covers this on Linux"
fi

step "run.sh fails when archivebot fails, and a dry run writes nothing"
BEFORE="$(cat public_html/out public_html/status 2>/dev/null | shasum || true)"
set +e
RUN_OUT="$(PYTHON=/usr/bin/false PYWIKI="$PYWIKI" VENV="$PWD/.venv" ./run.sh --simulate 2>&1)"
rc=$?
set -e
[[ $rc -eq 1 ]] || fail "run.sh exited $rc with a failing interpreter, expected 1"
grep -q 'SIMULATED run started' <<<"$RUN_OUT" || fail "run.sh did not write the run header"
grep -q 'ERROR: archivebot -lang:no' <<<"$RUN_OUT" || fail "run.sh did not report the failure"
AFTER="$(cat public_html/out public_html/status 2>/dev/null | shasum || true)"
[[ "$BEFORE" == "$AFTER" ]] || fail "--simulate modified public_html/out or status"
echo "ok: non-zero exit, an ERROR line, and no side effects"

step "status page contract (node)"
if command -v node >/dev/null 2>&1; then
    node tests/status-page.test.mjs || fail "status page contract tests"
else
    echo "skipped: node not installed"
fi

step "serve.py serves the page from a temp copy"
SERVE_DIR="$(mktemp -d)"
PORT=$(( (RANDOM % 1000) + 20000 ))
cp public_html/index.html "$SERVE_DIR/"
printf 'false\n' > "$SERVE_DIR/status"
printf 'test placeholder\n' > "$SERVE_DIR/out"
.venv/bin/python serve.py --bind 127.0.0.1 --port "$PORT" --root "$SERVE_DIR" >/dev/null 2>&1 &
SERVER_PID=$!
disown "$SERVER_PID" 2>/dev/null || true
cleanup() {
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    rm -f "$SERVE_DIR/index.html" "$SERVE_DIR/status" "$SERVE_DIR/out"
    rmdir "$SERVE_DIR" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 50); do
    curl -sf -o /dev/null "http://127.0.0.1:$PORT/" && break
    sleep 0.1
done

INDEX_HEADERS="$(curl -sfi "http://127.0.0.1:$PORT/" || true)"
STATUS_HEADERS="$(curl -sfi "http://127.0.0.1:$PORT/status?q=$RANDOM" || true)"
OUT_HEADERS="$(curl -sfi "http://127.0.0.1:$PORT/out?q=$RANDOM" || true)"

grep -qi '^content-type: text/html' <<<"$INDEX_HEADERS" || fail "/ is not text/html"
grep -q 'ArkivBot2' <<<"$INDEX_HEADERS" || fail "/ does not serve index.html"
grep -qi '^x-content-type-options: nosniff' <<<"$INDEX_HEADERS" || fail "nosniff missing"
grep -qi '^content-type: text/plain; charset=utf-8' <<<"$STATUS_HEADERS" || fail "/status is not text/plain"
grep -qi '^cache-control: no-store' <<<"$STATUS_HEADERS" || fail "/status is cacheable"
grep -q 'false' <<<"$STATUS_HEADERS" || fail "/status ignores query strings or misses the body"
grep -qi '^content-type: text/plain; charset=utf-8' <<<"$OUT_HEADERS" || fail "/out is not text/plain"
grep -q 'test placeholder' <<<"$OUT_HEADERS" || fail "/out misses the body"
echo "ok"

step "serve.py answers /status before the first run"
rm -f "$SERVE_DIR/status"
MISSING="$(curl -sfi "http://127.0.0.1:$PORT/status" || true)"
grep -q '^HTTP/.* 200' <<<"$MISSING" || fail "/status is not 200 when the file is absent (health check would fail)"
grep -q 'unknown' <<<"$MISSING" || fail "/status body should be 'unknown' when absent"
echo "ok"

step "serve.py only serves the allowlist"
TRAVERSAL="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/../run.sh" || true)"
DOTFILE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/.gitignore" || true)"
LOGFILE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/serve.py" || true)"
[[ "$TRAVERSAL" == 404 ]] || fail "/../run.sh answered $TRAVERSAL, expected 404"
[[ "$DOTFILE" == 404 ]] || fail "/.gitignore answered $DOTFILE, expected 404"
[[ "$LOGFILE" == 404 ]] || fail "/serve.py answered $LOGFILE, expected 404"
rm -f "$SERVE_DIR/status"
ln -s /etc/hosts "$SERVE_DIR/status"
SYMLINK="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/status" || true)"
[[ "$SYMLINK" == 404 ]] || fail "a symlinked /status answered $SYMLINK, expected 404"
rm -f "$SERVE_DIR/status"
echo "ok: traversal, dotfiles and symlinks are refused"

step "filter-log.py keeps the story, drops paths and frames"
FILTER_OUT="$(.venv/bin/python filter-log.py <<'SAMPLE'
>>> [[no:Wikipedia:Torget]] <<<
Archiving 31 thread(s).
ERROR: Error occurred while processing page [[no:Diskusjon:Neger]]
Traceback (most recent call last):
  File "/data/project/arkivbot2/pywikibot/scripts/archivebot.py", line 1003, in process_page
    archiver.run()
pywikibot.exceptions.TranslationError: No translation available for key
SAMPLE
)"
grep -q '^ERROR:' <<<"$FILTER_OUT" || fail "filter dropped the ERROR summary"
grep -q 'TranslationError' <<<"$FILTER_OUT" || fail "filter dropped the exception line"
grep -q 'File "' <<<"$FILTER_OUT" && fail "filter kept a traceback frame"
grep -q '/data/project' <<<"$FILTER_OUT" && fail "filter kept an absolute path"
grep -qE '^[[:space:]]*[~^]{2,}[[:space:]]*$' <<<"$FILTER_OUT" && fail "filter kept traceback carets"
echo "ok"

step "archivebot_wrapper neutralises only blacklisted links"
PYTHONPATH="$PYWIKI" uv run --quiet python - <<'PY'
import importlib.util

spec = importlib.util.spec_from_file_location('wrapper', 'archivebot_wrapper.py')
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)

cases = [
    ('See https://archive.today/abc123 for the source.', 'archive.today', 1),
    ('Two: https://archive.today/a and archive.today/b.)', 'archive.today', 2),
    ('Escaped: <nowiki>https://archive.today/x</nowiki>', 'archive.today', 0),
    ('Other host: https://example.org/archive.today-ish', 'archive.today', 0),
    # The blacklist matches raw text, so a bare domain can be the trigger.
    ('Bare domain in text: archive.today/x', 'archive.today', 1),
    ('Permalink https://en.wikipedia.org/wiki/Foo_(bar)', 'archive.today', 0),
]
for text, needle, expected in cases:
    _, count = wrapper.neutralise(text, needle)
    assert count == expected, f'{text!r}: wrapped {count}, expected {expected}'

# A save can be rejected for a different site on each attempt.
import contextlib
import io

from pywikibot.exceptions import SpamblacklistError


class StubPage:
    def __init__(self, text):
        self.text = text

    def title(self):
        return 'Diskusjon:Test'


page = StubPage('A https://archive.today/x and B https://t.co/z')
blocked = ['archive.today', 't.co']
attempts = []


def fake_save(self, *args, **kwargs):
    attempts.append(self.text)
    if blocked:
        raise SpamblacklistError(self, blocked.pop(0))
    return 'saved'


wrapper._original_save = fake_save
with contextlib.redirect_stderr(io.StringIO()):
    result = wrapper.save_with_fallback(page, 'summary')

assert result == 'saved', result
assert len(attempts) == 3, f'expected 3 save attempts, got {len(attempts)}'
assert '<nowiki>https://archive.today/x</nowiki>' in page.text
assert '<nowiki>https://t.co/z</nowiki>' in page.text
print('ok: 6 link cases, and two blocked sites resolved in one save')
PY

step "python syntax"
for file in serve.py filter-log.py archivebot_wrapper.py check-oauth.py web-preview.py audit-templates.py user-config.py.example; do
    uv run --quiet python -m py_compile "$file" || fail "py_compile $file"
done
echo "ok"

step "linters"
if [[ "${SKIP_LINT:-0}" == 1 ]]; then
    echo "skipped: SKIP_LINT=1"
elif ! command -v uvx >/dev/null 2>&1; then
    echo "skipped: uvx not found"
else
    uvx --quiet --from shellcheck-py shellcheck -x \
        run.sh bootstrap-venv.sh test-local.sh health-check.sh tests/tracked-files.test.sh \
        || fail "shellcheck"
    uvx --quiet ruff check --no-cache . \
        || fail "ruff"
    uvx --quiet mypy --strict --python-version 3.13 --no-incremental \
        --ignore-missing-imports serve.py check-oauth.py web-preview.py audit-templates.py \
        || fail "mypy"
    echo "ok"
fi

if [[ "$LIVE" == 1 ]]; then
    step "archivebot -simulate on no.wikipedia (read-only)"
    if ! uv run --quiet python "$PYWIKI/scripts/archivebot.py" -lang:no -simulate Autoarkivering; then
        echo
        echo "⚠️  the simulate run failed."
        echo "    OAuth/NoUsernameError (e.g. 'Invalid signature')? Check the credential"
        echo "    shapes without exposing them:  uv run python check-oauth.py"
        exit 1
    fi
fi

printf '\n✅ all local checks passed\n'
