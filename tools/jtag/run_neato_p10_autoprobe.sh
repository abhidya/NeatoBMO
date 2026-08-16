#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  tools/jtag/run_neato_p10_autoprobe.sh --session-dir DIR --label LABEL [--speed KHZ] [--runs N]

Runs read-only OpenOCD JTAG autoprobe captures for Cruz P10.

Examples:
  tools/jtag/run_neato_p10_autoprobe.sh \
    --session-dir captures/jtag/jtag-p10-YYYYMMDDTHHMMSSZ \
    --label pre-update --runs 3

  tools/jtag/run_neato_p10_autoprobe.sh \
    --session-dir captures/jtag/jtag-p10-YYYYMMDDTHHMMSSZ \
    --label during-update --speed 10 --runs 1

The only OpenOCD operation is: init; scan_chain; shutdown
USAGE
}

session_dir=""
label=""
speed="10"
runs="1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session-dir)
      session_dir="${2:?missing --session-dir value}"
      shift 2
      ;;
    --label)
      label="${2:?missing --label value}"
      shift 2
      ;;
    --speed)
      speed="${2:?missing --speed value}"
      shift 2
      ;;
    --runs)
      runs="${2:?missing --runs value}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$session_dir" || -z "$label" ]]; then
  usage >&2
  exit 2
fi

if [[ ! "$label" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "label must contain only letters, digits, '.', '_', or '-'" >&2
  exit 2
fi

if [[ ! "$speed" =~ ^[0-9]+$ || "$speed" -lt 1 || "$speed" -gt 100 ]]; then
  echo "speed must be an integer from 1 through 100 kHz" >&2
  exit 2
fi

if [[ ! "$runs" =~ ^[0-9]+$ || "$runs" -lt 1 ]]; then
  echo "runs must be a positive integer" >&2
  exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"
config="$repo_root/tools/jtag/neato_p10_autoprobe.cfg"
mkdir -p "$session_dir"

overall_status=0
for run in $(seq 1 "$runs"); do
  log="$session_dir/openocd-${label}-${speed}khz-run${run}.log"
  if [[ -e "$log" ]]; then
    echo "refusing to overwrite existing log: $log" >&2
    exit 1
  fi

  set +e
  {
    printf '# UTC %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '# label %s\n' "$label"
    printf '# speed_khz %s\n' "$speed"
    printf '# operation init; scan_chain; shutdown\n'
    openocd \
      -f "$config" \
      -c "adapter speed $speed" \
      -c "init; scan_chain; shutdown"
  } >"$log" 2>&1

  status=$?
  set -e
  if [[ "$status" -ne 0 ]]; then
    overall_status="$status"
  fi
  echo "$log status=$status"
done

exit "$overall_status"
