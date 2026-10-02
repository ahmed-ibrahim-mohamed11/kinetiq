# One command: evaluate the LEGACY 2D baseline (sanity reference, not the research baseline).
# Packages ai/outputs/dataset_build/*/reps.csv and scores it against the manual annotations.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$py = ".\.venv\Scripts\python.exe"
& $py ai\research\legacy_2d_predictions.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $py ai\research\eval_rep_segmentation.py `
    --gt   ai\outputs\manual_rep_annotations.csv `
    --pred ai\outputs\baselines\legacy_2d_predictions.csv `
    --match pull iou `
    --name legacy_2d_elbow_span_fsm `
    --out  ai\outputs\baselines\legacy_2d_elbow_span_fsm.json
exit $LASTEXITCODE
