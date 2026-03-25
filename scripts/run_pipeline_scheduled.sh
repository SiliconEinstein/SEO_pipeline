#!/usr/bin/env bash
set -euo pipefail

# Run from project root (cron can use: cd /opt/SEO_pipeline && bash scripts/run_pipeline_scheduled.sh)
# Fixed batch size for scheduled run.
TOP_N=100
UPLOAD_HOOK="scripts/upload_optimized.py"

RUN_ID="$(date '+%Y%m%d_%H%M%S')"
LOG_DIR="logs"
ARCHIVE_DIR="output_archive"
LOG_FILE="${LOG_DIR}/run_${RUN_ID}.log"
SNAPSHOT_DIR="${ARCHIVE_DIR}/output_${RUN_ID}"

mkdir -p "${LOG_DIR}" "${ARCHIVE_DIR}" "output"

run_upload_hook() {
  if [[ -f "${UPLOAD_HOOK}" ]]; then
    echo "upload: running hook ${UPLOAD_HOOK}"
    uv run python "${UPLOAD_HOOK}" "output" "${RUN_ID}" "${TOP_N}"
    echo "upload: hook finished"
    return 0
  fi
  echo "upload: hook missing: ${UPLOAD_HOOK}" >&2
  return 1
}

{
  echo "start: $(date '+%F %T %z') run_id=${RUN_ID} top=${TOP_N}"
  uv run python main.py all --top "${TOP_N}"
  run_upload_hook

  # On successful run: rotate output directory to a timestamped archive,
  # then create a fresh empty output for next run.
  mv output "${SNAPSHOT_DIR}"
  mkdir -p output

  printf "run_id=%s\ntop_n=%s\nrun_at=%s\n" \
    "${RUN_ID}" "${TOP_N}" "$(date '+%F %T %z')" > "${SNAPSHOT_DIR}/run_meta.txt"
  echo "done: $(date '+%F %T %z')"
} >> "${LOG_FILE}" 2>&1

echo "ok: ${LOG_FILE}"
