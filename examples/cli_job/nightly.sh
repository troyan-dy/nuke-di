#!/bin/sh
# What a scheduler runs, from examples/: sh cli_job/nightly.sh 2026-10-01
# The exit code of the job tells whether running it again can help
uv run python -m cli_job.export --since "$1" --output "${TMPDIR:-/tmp}/orders.csv"
code=$?
case $code in
  0)       echo "nightly: exported" ;;
  2)       echo "nightly: fix the command line, retrying will not help" ;;
  130|143) echo "nightly: interrupted, safe to run again" ;;
  *)       echo "nightly: failed, see the log" ;;
esac
exit $code
