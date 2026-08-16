#!/usr/bin/env bash
# P10 JTAG halt + SRAM/SDRAM dump for the Cruz Rev113.
#
# Read-only: halts the core, dumps internal SRAM and the SDRAM window, never
# writes flash.  Run it only AFTER `run_neato_p10_autoprobe.sh` shows a stable
# IDCODE.  If scan_chain reports "all ones", this script refuses to continue.
#
# Usage:
#   run_neato_p10_halt.sh [speed_khz] [sdram_bytes]
#     speed_khz   JTAG clock in kHz (default 10; raise only after a clean scan)
#     sdram_bytes SDRAM dump length (default 0x1000 = 4 KiB head)
# Required environment (copied from at least three identical auto-probes):
#   NEATO_P10_IDCODE=0x........ NEATO_P10_IRLEN=...
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
session_id="${SESSION_ID:-jtag-halt-$(date +%Y%m%dT%H%M%SZ)}"
speed_khz="${1:-10}"
sdram_bytes="${2:-0x1000}"
: "${NEATO_P10_IDCODE:?set from a stable measured TAP; no guessed ID is accepted}"
: "${NEATO_P10_IRLEN:?set from a stable measured TAP; no guessed IR length is accepted}"
if [[ ! "${NEATO_P10_IDCODE}" =~ ^0x[0-9A-Fa-f]{8}$ ]]; then
  echo "invalid NEATO_P10_IDCODE: expected 0x plus eight hexadecimal digits" >&2
  exit 2
fi
if [[ ! "${NEATO_P10_IRLEN}" =~ ^[1-9][0-9]*$ ]]; then
  echo "invalid NEATO_P10_IRLEN: expected a positive integer" >&2
  exit 2
fi
if [[ ! "${session_id}" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "invalid SESSION_ID: use only letters, digits, dot, underscore, or hyphen" >&2
  exit 2
fi
private_base="${NEATO_JTAG_PRIVATE_ROOT:-/Volumes/2TB/neato-jtag-private}"
mkdir -p "${private_base}"
private_base="$(cd "${private_base}" && pwd -P)"
log_dir="${private_base}/${session_id}"
case "${log_dir}/" in
  "${repo_root}/"*)
    echo "refusing to store private memory dumps inside the Git worktree" >&2
    exit 2
    ;;
esac
mkdir -p "${log_dir}"
chmod 700 "${log_dir}"

cfg="${script_dir}/neato_p10_halt.cfg"
sram0="${log_dir}/sram0-0x00200000.bin"
sram1="${log_dir}/sram1-0x00300000.bin"
sdram="${log_dir}/sdram-0x20000000.bin"
log_file="${log_dir}/openocd-halt-${speed_khz}khz.log"
preflight_log="${log_dir}/openocd-preflight-${speed_khz}khz.log"

echo "[halt] session=${session_id} speed=${speed_khz}kHz sdram_bytes=${sdram_bytes}"
echo "[halt] dumping SRAM0 0x00200000, SRAM1 0x00300000 (16 KiB each), SDRAM head 0x20000000"

openocd \
  -c "set CPUTAPID ${NEATO_P10_IDCODE}" \
  -c "set CPUIRLEN ${NEATO_P10_IRLEN}" \
  -f "${cfg}" \
  -c "adapter speed ${speed_khz}" \
  -c "init; scan_chain; shutdown" \
  2>&1 | tee "${preflight_log}"

if ! grep -q "tap/device found" "${preflight_log}" || \
   grep -Eiq "all ones|all zeroes|unexpected idcode|examination failed" "${preflight_log}"; then
  echo "refusing halt: current preflight did not reproduce a stable TAP" >&2
  exit 3
fi

resume_target() {
  openocd \
    -c "set CPUTAPID ${NEATO_P10_IDCODE}" \
    -c "set CPUIRLEN ${NEATO_P10_IRLEN}" \
    -f "${cfg}" \
    -c "adapter speed ${speed_khz}" \
    -c "init; resume; shutdown" >/dev/null 2>&1 || true
}
trap resume_target EXIT

openocd \
  -c "set CPUTAPID ${NEATO_P10_IDCODE}" \
  -c "set CPUIRLEN ${NEATO_P10_IRLEN}" \
  -f "${cfg}" \
  -c "adapter speed ${speed_khz}" \
  -c "init" \
  -c "halt" \
  -c "reg" \
  -c "dump_image ${sram0} 0x00200000 0x4000" \
  -c "dump_image ${sram1} 0x00300000 0x4000" \
  -c "dump_image ${sdram} 0x20000000 ${sdram_bytes}" \
  -c "resume" \
  -c "shutdown" \
  2>&1 | tee "${log_file}"

trap - EXIT
echo "[halt] done. private logs+dumps in ${log_dir}"
