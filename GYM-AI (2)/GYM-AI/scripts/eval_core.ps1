# One command: run the ai.core pipeline (nested LOVO) and score it with the single evaluator.
# This is the CURRENT baseline command since Phase 2 (the Phase 0 frozen-script command is eval_v2.ps1).
param(
    [string]$OutDir = "ai\outputs\rep_eval_core",
    [string]$Name = "core_hsmm_nested_lovo"
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$py = ".\.venv\Scripts\python.exe"
& $py ai\research\run_core_lovo.py --out_dir $OutDir
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $py ai\research\eval_rep_segmentation.py `
    --gt   ai\outputs\manual_rep_annotations.csv `
    --pred (Join-Path $OutDir "predicted_reps_with_arm_timing.csv") `
    --match pull iou `
    --name $Name `
    --out  (Join-Path "ai\outputs\baselines" ($Name + ".json"))
exit $LASTEXITCODE
