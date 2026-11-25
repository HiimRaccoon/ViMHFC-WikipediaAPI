#!/usr/bin/env bash
#SBATCH --job-name=btt
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
: "${BTT_REPO:?Tested checkout}"
: "${BTT_COMMIT:?Tested commit}"
: "${BTT_STAGE:?New persistent directory}"
: "${BTT_SOURCE:?Stopped source campaign}"
: "${BTT_BUNDLE:?Immutable source bundle}"
: "${BTT_PYTHON:?Locked Python 3.12}"
: "${BTT_RUNTIME:?Pinned runtime}"
: "${BTT_MODEL:?Pinned model}"
[[ "${FAST20K_EGRESS_APPROVED:-0}" == 1 && "${SLURM_GPUS:-0}" == 0 ]]
export CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=3 PYTHONUNBUFFERED=1
unset VIMFC_OFFLINE
cd "$BTT_REPO"
[[ "$(git rev-parse HEAD)" == "$BTT_COMMIT" && -z "$(git status --porcelain --untracked-files=no)" ]]
scratch="/local/btt-${USER}-${SLURM_JOB_ID}"
mkdir -m 700 "$scratch"
[[ "$(findmnt -n -o FSTYPE -T "$scratch")" == ext4 ]]
cp "$BTT_MODEL" "$scratch/model.gguf"
cp -R --no-preserve=all "$(dirname "$BTT_RUNTIME")" "$scratch/runtime"
chmod u+x "$scratch/runtime/llama-server"
logs="$BTT_STAGE/logs/$SLURM_JOB_ID"; mkdir -p "$logs"
"$BTT_PYTHON" -B scripts/btt.py --bundle "$BTT_BUNDLE" --source-campaign "$BTT_SOURCE" \
  --workspace "$scratch/workspace" --durable "$BTT_STAGE/campaign" --output "$BTT_STAGE/corpus" \
  --logs "$logs" --runtime "$scratch/runtime/llama-server" --model "$scratch/model.gguf" --allow-network &
worker_pid=$!
trap 'kill -TERM "$worker_pid" 2>/dev/null || true; wait "$worker_pid" || true; exit 130' TERM INT
wait "$worker_pid"
