#!/bin/bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate diffsim
python3 /home/ubuntu/.openclaw/workspace/control-plane/workspaces/engineer/xla_preflight.py --verbose --require-gpu
