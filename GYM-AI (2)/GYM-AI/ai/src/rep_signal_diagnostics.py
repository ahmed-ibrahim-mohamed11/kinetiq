from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from scipy.signal import (
    savgol_filter,
    find_peaks
)


# =========================================================
# 1. Paths
# =========================================================

AI_DIR = Path(__file__).resolve().parent.parent

BUILD_DIR = (
    AI_DIR
    / "outputs"
    / "dataset_build"
)

DATA_DIR = (
    AI_DIR
    / "data"
    / "seated_row"
)


VIDEOS = [

    ("correct_01", "correct"),
    ("correct_02", "correct"),
    ("correct_03", "correct"),

    ("asymmetry_01", "asymmetry"),
    ("asymmetry_02", "asymmetry"),
    ("asymmetry_03", "asymmetry"),
]


# =========================================================
# 2. Helpers
# =========================================================

def get_points(df, name):

    return np.column_stack([

        df[f"{name}_x"].to_numpy(
            dtype=float
        ),

        df[f"{name}_y"].to_numpy(
            dtype=float
        )
    ])


def clean_signal(signal):

    return (

        pd.Series(signal)

        .replace(
            [np.inf, -np.inf],
            np.nan
        )

        .interpolate()

        .bfill()

        .ffill()

        .to_numpy(
            dtype=float
        )
    )


def smooth_signal(
    signal,
    fps
):

    signal = clean_signal(
        signal
    )


    # About half a second smoothing
    window = int(
        round(
            fps * 0.5
        )
    )


    if window % 2 == 0:
        window += 1


    window = max(
        window,
        7
    )


    if window >= len(signal):

        window = (
            len(signal) - 1
        )

        if window % 2 == 0:
            window -= 1


    if window < 5:

        return signal


    return savgol_filter(
        signal,
        window_length=window,
        polyorder=3
    )


# =========================================================
# 3. Peak Detector
# =========================================================

def detect_peaks(
    signal,
    fps
):

    signal = smooth_signal(
        signal,
        fps
    )


    p05 = np.percentile(
        signal,
        5
    )

    p95 = np.percentile(
        signal,
        95
    )


    signal_range = (
        p95 - p05
    )


    # Ignore tiny noise
    prominence = max(
        signal_range * 0.12,
        1e-6
    )


    # Two reps cannot reasonably happen
    # inside this very short interval.
    minimum_distance = max(
        int(
            fps * 0.8
        ),
        1
    )


    # High peaks
    high_peaks, high_properties = (
        find_peaks(
            signal,
            distance=minimum_distance,
            prominence=prominence
        )
    )


    # Low peaks
    low_peaks, low_properties = (
        find_peaks(
            -signal,
            distance=minimum_distance,
            prominence=prominence
        )
    )


    return (
        signal,
        high_peaks,
        low_peaks
    )


# =========================================================
# 4. Analyze One Video
# =========================================================

def analyze_video(
    stem,
    label
):

    keypoints_path = (
        BUILD_DIR
        / stem
        / "keypoints.csv"
    )


    video_path = (
        DATA_DIR
        / label
        / f"{stem}.mp4"
    )


    if not keypoints_path.exists():

        print(
            f"Missing keypoints: "
            f"{keypoints_path}"
        )

        return []


    df = pd.read_csv(
        keypoints_path
    )


    # -----------------------------------------
    # FPS
    # -----------------------------------------

    fps = 30.0

    if video_path.exists():

        cap = cv2.VideoCapture(
            str(video_path)
        )

        detected_fps = cap.get(
            cv2.CAP_PROP_FPS
        )

        cap.release()


        if detected_fps > 0:

            fps = detected_fps


    # -----------------------------------------
    # Points
    # -----------------------------------------

    LS = get_points(
        df,
        "left_shoulder"
    )

    RS = get_points(
        df,
        "right_shoulder"
    )

    LE = get_points(
        df,
        "left_elbow"
    )

    RE = get_points(
        df,
        "right_elbow"
    )


    shoulder_width = np.linalg.norm(
        LS - RS,
        axis=1
    )


    shoulder_width = np.maximum(
        shoulder_width,
        1e-6
    )


    # =====================================================
    # SIGNAL 1
    #
    # Current method:
    # distance between elbows
    # =====================================================

    elbow_span = (

        np.abs(
            LE[:, 0]
            -
            RE[:, 0]
        )

        /

        shoulder_width
    )


    # =====================================================
    # SIGNAL 2
    #
    # Average distance of each elbow
    # from its OWN shoulder.
    #
    # This is much less dependent on
    # left/right symmetry.
    # =====================================================

    left_arm = (

        np.linalg.norm(
            LE - LS,
            axis=1
        )

        /

        shoulder_width
    )


    right_arm = (

        np.linalg.norm(
            RE - RS,
            axis=1
        )

        /

        shoulder_width
    )


    average_arm_distance = (

        left_arm
        +
        right_arm

    ) / 2


    # =====================================================
    # SIGNAL 3
    #
    # Common symmetric motion.
    #
    # Mirror the right side,
    # then average both elbow motions.
    # PCA extracts the dominant movement direction.
    # =====================================================

    left_x = (

        LE[:, 0]
        -
        LS[:, 0]

    ) / shoulder_width


    left_y = (

        LE[:, 1]
        -
        LS[:, 1]

    ) / shoulder_width


    right_x = (

        RE[:, 0]
        -
        RS[:, 0]

    ) / shoulder_width


    right_y = (

        RE[:, 1]
        -
        RS[:, 1]

    ) / shoulder_width


    # Mirror right side
    right_x = (
        -right_x
    )


    common_x = (

        left_x
        +
        right_x

    ) / 2


    common_y = (

        left_y
        +
        right_y

    ) / 2


    X = np.column_stack([
        clean_signal(common_x),
        clean_signal(common_y)
    ])


    # Standardization
    X = (
        X
        -
        np.mean(
            X,
            axis=0
        )
    )


    std = np.std(
        X,
        axis=0
    )


    std = np.maximum(
        std,
        1e-6
    )


    X = X / std


    # PCA using SVD
    _, _, vt = np.linalg.svd(
        X,
        full_matrices=False
    )


    pca_signal = (
        X
        @
        vt[0]
    )


    signals = {

        "elbow_span":
            elbow_span,

        "average_arm_distance":
            average_arm_distance,

        "common_motion_pca":
            pca_signal
    }


    results = []


    print()
    print(
        "=============================================="
    )

    print(
        f"{stem}.mp4"
    )

    print(
        f"Ground Truth: 5 reps"
    )

    print(
        "=============================================="
    )


    for (
        signal_name,
        signal
    ) in signals.items():


        (
            smoothed,
            high_peaks,
            low_peaks

        ) = detect_peaks(
            signal,
            fps
        )


        high_times = (
            high_peaks
            /
            fps
        )


        low_times = (
            low_peaks
            /
            fps
        )


        print()
        print(
            signal_name
        )

        print(
            f"High peaks: "
            f"{len(high_peaks)}"
        )

        print(
            f"Low peaks : "
            f"{len(low_peaks)}"
        )


        print(
            "High peak times:",
            np.round(
                high_times,
                2
            )
        )


        print(
            "Low peak times :",
            np.round(
                low_times,
                2
            )
        )


        results.append({

            "video":
                stem,

            "label":
                label,

            "signal":
                signal_name,

            "high_peak_count":
                len(
                    high_peaks
                ),

            "low_peak_count":
                len(
                    low_peaks
                )
        })


    return results


# =========================================================
# 5. Run All Videos
# =========================================================

all_results = []


for (
    stem,
    label
) in VIDEOS:

    results = analyze_video(
        stem,
        label
    )

    all_results.extend(
        results
    )


# =========================================================
# 6. Save Summary
# =========================================================

results_df = pd.DataFrame(
    all_results
)


output_path = (

    AI_DIR
    / "outputs"
    / "rep_signal_diagnostics.csv"
)


results_df.to_csv(
    output_path,
    index=False
)


print()
print()
print(
    "##############################################"
)

print(
    "SUMMARY"
)

print(
    "##############################################"
)

print()

print(
    results_df.to_string(
        index=False
    )
)

print()
print(
    f"Saved to:\n{output_path}"
)