#!/usr/bin/env bash
# Template only. Account/partition belong to UIT and must be supplied to sbatch.
#SBATCH --job-name=vifc-annotation-10k
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --signal=B:TERM@120
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err

set -euo pipefail
umask 077
: "${COLLECTION_REVIEWED:?Set only after review authorizes this server collection}"
[[ "$COLLECTION_REVIEWED" == 1 ]]
: "${COLLECTION_REPO:?Absolute path to the reviewed repository}"
: "${COLLECTION_BUNDLE:?Read-only verified bundle directory}"
: "${COLLECTION_WORKSPACE:?Private writable workspace directory}"
: "${COLLECTION_PYTHON:?Absolute path to Python 3.12+ in the prepared venv}"
cd "$COLLECTION_REPO"
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-4}"
export PYTHONUNBUFFERED=1
mode="${COLLECTION_MODE:-process}"
args=()
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
  --mode "$mode" --through 10000 --batch-size 250 --max-requests 1000 "${args[@]}" &
worker_pid=$!
trap 'kill -TERM "$worker_pid" 2>/dev/null || true; wait "$worker_pid" || true; exit 130' TERM INT
wait "$worker_pid"
