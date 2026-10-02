# One command: rebuild the MotionBERT 3D pose inputs for the 6 research videos (Path B).
# YOLO11n-pose -> COCO-17 -> H36M-17 -> crop_scale normalisation -> MotionBERT -> ai/data/poses3d/{video}.npy
# Requires: yolo11n-pose.pt (auto-downloaded by ultralytics) and
#           ai/models/motionbert/FT_MB_release_MB_ft_h36m/best_epoch.bin (see ai/models/README.md).
param(
    [switch]$Overwrite
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$py = ".\.venv\Scripts\python.exe"
$flags = @()
if ($Overwrite) { $flags += "--overwrite" }
& $py ai\research\extract_poses3d.py @flags
exit $LASTEXITCODE
