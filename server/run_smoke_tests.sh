#!/usr/bin/env bash
# Run every server smoke test and fail if any of them fails.
# Local: ./run_smoke_tests.sh          (uses .venv if present)
# CI:    PYTHON=python ./run_smoke_tests.sh
set -u
cd "$(dirname "$0")"

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x .venv/bin/python ]; then
    PYTHON=.venv/bin/python
  else
    PYTHON=python3
  fi
fi

fail=0
for t in *smoke_test*.py; do
  echo "=== $t ==="
  if ! "$PYTHON" "$t"; then
    echo "FAIL: $t"
    fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  echo "RESULT: smoke tests FAILED"
  exit 1
fi
echo "RESULT: all smoke tests passed"
