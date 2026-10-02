# One command: reproduce the v2 HSMM nested leave-one-video-out baseline, then score it with the
# single evaluator (both matching rules). Requires MotionBERT (T,17,3) H36M .npy poses in -PosesDir.
param(
    [string]$PosesDir = "ai\data\poses3d",
    [double]$Fps = 30,
    [string]$OutDir = "ai\outputs\rep_eval_v2"
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Test-Path $PosesDir)) {
    Write-Error "Missing $PosesDir - MotionBERT (T,17,3) H36M .npy pose files are required (docs/PROJECT_STATE.md, blocker B1)."
    exit 1
}
$py = ".\.venv\Scripts\python.exe"
& $py ai\research\rep_segmentation_v2_hsmm.py `
    --poses_dir $PosesDir `
    --annotations ai\outputs\manual_rep_annotations.csv `
    --fps $Fps `
    --out_dir $OutDir
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $py ai\research\eval_rep_segmentation.py `
    --gt   ai\outputs\manual_rep_annotations.csv `
    --pred (Join-Path $OutDir "predicted_reps_with_arm_timing.csv") `
    --match pull iou `
    --name v2_hsmm_nested_lovo `
    --out  ai\outputs\baselines\v2_hsmm_nested_lovo.json
exit $LASTEXITCODE
