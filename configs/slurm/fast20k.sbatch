#!/usr/bin/env bash
#SBATCH --job-name=vifc-fast20k
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --gres=none
#SBATCH --time=3-00:00:00
#SBATCH --signal=B:TERM@600
#SBATCH --no-requeue
#SBATCH --output=slurm-%x-%j.out
#SBATCH --error=slurm-%x-%j.err

set -euo pipefail
umask 077
[[ "${SLURM_RESTART_COUNT:-0}" == 0 ]]
: "${FAST20K_REPO:?Exact tested checkout}"
: "${FAST20K_COMMIT:?Exact tested commit SHA}"
: "${FAST20K_BUNDLE:?Immutable source bundle}"
: "${FAST20K_STAGE:?Private persistent campaign directory}"
: "${FAST20K_PYTHON:?Python 3.12 locked environment}"
: "${FAST20K_RUNTIME:?Pinned llama-server binary}"
: "${FAST20K_MODEL:?Pinned Qwen3.5-4B GGUF}"
[[ "${FAST20K_EGRESS_APPROVED:-0}" == 1 ]]
[[ "${SLURM_GPUS:-0}" == 0 ]]
export CUDA_VISIBLE_DEVICES=''
export OMP_NUM_THREADS=3 PYTHONUNBUFFERED=1
unset VIMFC_OFFLINE
cd "$FAST20K_REPO"
[[ "$(git rev-parse HEAD)" == "$FAST20K_COMMIT" ]]
[[ -z "$(git status --porcelain --untracked-files=no)" ]]
local_parent="/local/fast20k-${USER}-${SLURM_JOB_ID}"
mkdir -m 700 "$local_parent"
cp "$FAST20K_MODEL" "$local_parent/model.gguf"
# Copy bytes and symlinks without importing NFS permissions/ACL metadata.
cp -R --no-preserve=all "$(dirname "$FAST20K_RUNTIME")" "$local_parent/runtime"
chmod u+x "$local_parent/runtime/llama-server"
logs="$FAST20K_STAGE/logs/$SLURM_JOB_ID"
mkdir -p "$logs"
"$FAST20K_PYTHON" -B scripts/fast20k.py run \
  --bundle "$FAST20K_BUNDLE" --workspace "$local_parent/workspace" \
  --durable "$FAST20K_STAGE/campaign" --output "$FAST20K_STAGE/corpus" \
  --runtime "$local_parent/runtime/llama-server" --model "$local_parent/model.gguf" --logs "$logs" --allow-network &
worker_pid=$!
trap 'kill -TERM "$worker_pid" 2>/dev/null || true; wait "$worker_pid" || true; exit 130' TERM INT
wait "$worker_pid"
# No cleanup, automatic requeue, nested sbatch or follow-up job.
