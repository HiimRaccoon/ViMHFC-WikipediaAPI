#!/usr/bin/env bash
# Template only. Account/partition belong to UIT and must be supplied to sbatch.
#SBATCH --job-name=vifc-annotation-10k
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --signal=B:TERM@120
#SBATCH --no-requeue
# Paths are relative to Slurm's submission/--chdir directory, before this script runs.
#SBATCH --output=slurm-%x-%j.out
#SBATCH --error=slurm-%x-%j.err

set -euo pipefail
umask 077
: "${COLLECTION_REVIEWED:?Set only after review authorizes this server collection}"
[[ "$COLLECTION_REVIEWED" == 1 ]]
# A site can override --no-requeue; a restart still needs a new reviewed invocation.
if [[ "${SLURM_RESTART_COUNT:-0}" != 0 ]]; then
  echo '{"status":"preflight_error","reason":"slurm_requeue_requires_review","expected_stop":false}' >&2
  exit 3
fi
: "${COLLECTION_REPO:?Absolute path to the reviewed repository}"
: "${COLLECTION_BUNDLE:?Read-only verified bundle directory}"
: "${COLLECTION_WORKSPACE:?Private writable workspace directory}"
: "${COLLECTION_PYTHON:?Absolute path to Python 3.12+ in the prepared venv}"
cd "$COLLECTION_REPO"
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-4}"
export PYTHONUNBUFFERED=1
mode="${COLLECTION_MODE:-process}"
args=(--max-requests 1000)
if [[ "$mode" == acquire ]]; then
  : "${COLLECTION_EGRESS_REVIEWED:?Confirm compute node egress before acquisition}"
  [[ "$COLLECTION_EGRESS_REVIEWED" == 1 ]]
  unset VIMFC_OFFLINE
  args+=(--allow-network)
else
  [[ "$mode" == process ]]
  export VIMFC_OFFLINE=1
fi
# One bounded batch; repeat explicitly after checking its receipt. Never requeue itself.
"$COLLECTION_PYTHON" -B scripts/collection-readiness.py batch \
  --bundle "$COLLECTION_BUNDLE" --workspace "$COLLECTION_WORKSPACE" \
  --mode "$mode" --through 10000 --batch-size 250 "${args[@]}" &
worker_pid=$!
trap 'kill -TERM "$worker_pid" 2>/dev/null || true; wait "$worker_pid" || true; exit 130' TERM INT
wait "$worker_pid"
