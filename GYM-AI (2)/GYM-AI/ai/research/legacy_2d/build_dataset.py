from pathlib import Path

import cv2
import numpy as np
import pandas as pd
from scipy.signal import savgol_filter
from ultralytics import YOLO


# =========================================================
# 1. PROJECT PATHS
# =========================================================

AI_DIR = Path(__file__).resolve().parent.parent.parent

DATASET_DIR = (
    AI_DIR
    / "data"
    / "seated_row"
)

OUTPUT_DIR = (
    AI_DIR
    / "outputs"
    / "dataset_build"
)

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)

FINAL_DATASET_CSV = (
    AI_DIR
    / "outputs"
    / "seated_row_dataset.csv"
)


# =========================================================
# 2. DATASET CLASSES
# =========================================================

CLASSES = {
    "correct": (
        DATASET_DIR
        / "correct"
    ),

    "asymmetry": (
        DATASET_DIR
        / "asymmetry"
    )
}


# =========================================================
# 3. IMPORTANT JOINTS
# =========================================================

JOINTS = {

    "left_shoulder": 5,
    "right_shoulder": 6,

    "left_elbow": 7,
    "right_elbow": 8,

    "left_wrist": 9,
    "right_wrist": 10,

    "left_hip": 11,
    "right_hip": 12
}


# =========================================================
# 4. LOAD YOLO POSE MODEL
# =========================================================

print()
print("Loading YOLO Pose Model...")

model = YOLO(
    "yolo11n-pose.pt"
)

print("Model loaded successfully.")

print()


# =========================================================
# 5. HELPER
# =========================================================

def safe_savgol(
    signal,
    desired_window=31,
    polyorder=3
):

    signal = np.asarray(
        signal,
        dtype=float
    )

    n = len(signal)

    if n < 5:
        return signal


    window = min(
        desired_window,
        n
    )


    if window % 2 == 0:
        window -= 1


    minimum_window = (
        polyorder + 2
    )

    if minimum_window % 2 == 0:
        minimum_window += 1


    window = max(
        window,
        minimum_window
    )


    if window > n:

        window = n

        if window % 2 == 0:
            window -= 1


    if window <= polyorder:
        return signal


    return savgol_filter(
        signal,
        window_length=window,
        polyorder=polyorder
    )


# =========================================================
# 6. POSE EXTRACTION
# =========================================================

def extract_pose(
    video_path
):

    print()
    print(
        f"Processing pose: "
        f"{video_path.name}"
    )


    cap = cv2.VideoCapture(
        str(video_path)
    )


    if not cap.isOpened():

        raise RuntimeError(
            f"Could not open video:\n"
            f"{video_path}"
        )


    fps = cap.get(
        cv2.CAP_PROP_FPS
    )


    if fps <= 0:
        fps = 30.0


    rows = []

    frame_number = 0


    while True:

        success, frame = cap.read()

        if not success:
            break


        frame_number += 1


        # -----------------------------------------
        # YOLO Pose
        # -----------------------------------------

        results = model(
            frame,
            verbose=False
        )

        result = results[0]


        row = {
            "frame":
                frame_number
        }


        # -----------------------------------------
        # No person
        # -----------------------------------------

        if (
            result.keypoints is None
            or len(result.keypoints) == 0
        ):

            for joint_name in JOINTS:

                row[
                    f"{joint_name}_x"
                ] = np.nan

                row[
                    f"{joint_name}_y"
                ] = np.nan

                row[
                    f"{joint_name}_confidence"
                ] = np.nan


            rows.append(row)

            continue


        # -----------------------------------------
        # Get keypoints
        # -----------------------------------------

        keypoints = (
            result
            .keypoints
            .data
            .cpu()
            .numpy()
        )


        # -----------------------------------------
        # If multiple people are detected:
        # choose the person with largest bbox.
        # -----------------------------------------

        selected_person_index = 0


        if (
            result.boxes is not None
            and len(result.boxes) > 1
        ):

            boxes = (
                result
                .boxes
                .xyxy
                .cpu()
                .numpy()
            )


            areas = (

                (
                    boxes[:, 2]
                    -
                    boxes[:, 0]
                )

                *

                (
                    boxes[:, 3]
                    -
                    boxes[:, 1]
                )
            )


            selected_person_index = int(
                np.argmax(areas)
            )


        person = keypoints[
            selected_person_index
        ]


        # -----------------------------------------
        # Save joints
        # -----------------------------------------

        for (
            joint_name,
            joint_index
        ) in JOINTS.items():

            x = float(
                person[
                    joint_index
                ][0]
            )

            y = float(
                person[
                    joint_index
                ][1]
            )

            confidence = float(
                person[
                    joint_index
                ][2]
            )


            row[
                f"{joint_name}_x"
            ] = x

            row[
                f"{joint_name}_y"
            ] = y

            row[
                f"{joint_name}_confidence"
            ] = confidence


        rows.append(row)


    cap.release()


    pose_df = pd.DataFrame(
        rows
    )


    return (
        pose_df,
        fps
    )


# =========================================================
# 7. FIX MISSING VALUES
# =========================================================

def clean_pose_data(
    df
):

    coordinate_columns = []

    confidence_columns = []


    for joint_name in JOINTS:

        coordinate_columns.extend([
            f"{joint_name}_x",
            f"{joint_name}_y"
        ])

        confidence_columns.append(
            f"{joint_name}_confidence"
        )


    # Convert to numbers
    for column in (
        coordinate_columns
        +
        confidence_columns
    ):

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )


    # -----------------------------------------
    # Interpolate coordinates
    # -----------------------------------------

    df[
        coordinate_columns
    ] = (

        df[
            coordinate_columns
        ]

        .interpolate()

        .bfill()

        .ffill()
    )


    return df


# =========================================================
# 8. GET POINTS
# =========================================================

def get_points(
    df,
    joint_name
):

    return np.column_stack([

        df[
            f"{joint_name}_x"
        ].to_numpy(dtype=float),

        df[
            f"{joint_name}_y"
        ].to_numpy(dtype=float)
    ])


# =========================================================
# 9. CREATE MOVEMENT SIGNAL
# =========================================================

def create_movement_signal(
    df
):

    left_shoulder = get_points(
        df,
        "left_shoulder"
    )

    right_shoulder = get_points(
        df,
        "right_shoulder"
    )

    left_elbow = get_points(
        df,
        "left_elbow"
    )

    right_elbow = get_points(
        df,
        "right_elbow"
    )


    shoulder_width = np.linalg.norm(

        left_shoulder
        -
        right_shoulder,

        axis=1
    )


    shoulder_width = np.maximum(
        shoulder_width,
        1e-6
    )


    elbow_width = np.abs(

        left_elbow[:, 0]

        -

        right_elbow[:, 0]
    )


    signal = (

        elbow_width
        /
        shoulder_width
    )


    signal = (

        pd.Series(signal)

        .interpolate()

        .bfill()

        .ffill()

        .to_numpy()
    )


    signal = safe_savgol(
        signal
    )


    return signal


# =========================================================
# 10. SEGMENT REPS
# =========================================================

def segment_reps(
    signal
):

    LOW_THRESHOLD = np.percentile(
        signal,
        30
    )

    HIGH_THRESHOLD = np.percentile(
        signal,
        70
    )


    state = "WAITING"

    start_frame = None
    pull_frame = None

    reps = []

    rep_number = 0


    for (
        frame_index,
        value
    ) in enumerate(signal):

        frame_number = (
            frame_index + 1
        )


        # -----------------------------------------
        # WAITING
        # -----------------------------------------

        if state == "WAITING":

            if value <= LOW_THRESHOLD:

                start_frame = (
                    frame_number
                )

                state = "RELEASED"


        # -----------------------------------------
        # RELEASED -> PULL
        # -----------------------------------------

        elif state == "RELEASED":

            if value >= HIGH_THRESHOLD:

                pull_frame = (
                    frame_number
                )

                state = "PULLED"


        # -----------------------------------------
        # PULLED -> RELEASE
        # -----------------------------------------

        elif state == "PULLED":

            if value <= LOW_THRESHOLD:

                end_frame = (
                    frame_number
                )

                rep_number += 1


                reps.append({

                    "rep":
                        rep_number,

                    "start_frame":
                        start_frame,

                    "pull_frame":
                        pull_frame,

                    "end_frame":
                        end_frame,

                    "incomplete_return":
                        False
                })


                # End is next start
                start_frame = (
                    end_frame
                )

                pull_frame = None

                state = "RELEASED"


    # -----------------------------------------
    # Final incomplete repetition
    # -----------------------------------------

    if (
        state == "PULLED"
        and start_frame is not None
        and pull_frame is not None
    ):

        rep_number += 1


        reps.append({

            "rep":
                rep_number,

            "start_frame":
                start_frame,

            "pull_frame":
                pull_frame,

            "end_frame":
                len(signal),

            "incomplete_return":
                True
        })


    return (
        pd.DataFrame(reps),
        LOW_THRESHOLD,
        HIGH_THRESHOLD
    )


# =========================================================
# 11. FORM FEATURES
# =========================================================

def extract_form_features(
    pose_df,
    reps_df,
    movement_signal,
    fps,
    video_name,
    label
):

    left_shoulder = get_points(
        pose_df,
        "left_shoulder"
    )

    right_shoulder = get_points(
        pose_df,
        "right_shoulder"
    )

    left_elbow = get_points(
        pose_df,
        "left_elbow"
    )

    right_elbow = get_points(
        pose_df,
        "right_elbow"
    )

    left_hip = get_points(
        pose_df,
        "left_hip"
    )

    right_hip = get_points(
        pose_df,
        "right_hip"
    )


    # =====================================================
    # Shoulder Width
    # =====================================================

    shoulder_width = np.linalg.norm(

        left_shoulder
        -
        right_shoulder,

        axis=1
    )


    shoulder_width = np.maximum(
        shoulder_width,
        1e-6
    )


    # =====================================================
    # Shoulder asymmetry
    # =====================================================

    shoulder_asymmetry = (

        np.abs(

            left_shoulder[:, 1]

            -

            right_shoulder[:, 1]
        )

        /

        shoulder_width
    )


    # =====================================================
    # Torso shift
    # =====================================================

    shoulder_center = (

        left_shoulder
        +
        right_shoulder

    ) / 2


    hip_center = (

        left_hip
        +
        right_hip

    ) / 2


    torso_shift = (

        np.abs(

            shoulder_center[:, 0]

            -

            hip_center[:, 0]
        )

        /

        shoulder_width
    )


    # =====================================================
    # Elbow symmetry
    # =====================================================

    left_elbow_x = (

        left_elbow[:, 0]

        -

        left_shoulder[:, 0]

    ) / shoulder_width


    left_elbow_y = (

        left_elbow[:, 1]

        -

        left_shoulder[:, 1]

    ) / shoulder_width


    right_elbow_x = (

        right_elbow[:, 0]

        -

        right_shoulder[:, 0]

    ) / shoulder_width


    right_elbow_y = (

        right_elbow[:, 1]

        -

        right_shoulder[:, 1]

    ) / shoulder_width


    # Mirror right side
    right_elbow_x = (
        -right_elbow_x
    )


    elbow_asymmetry = np.sqrt(

        (
            left_elbow_x
            -
            right_elbow_x
        ) ** 2

        +

        (
            left_elbow_y
            -
            right_elbow_y
        ) ** 2
    )


    # =====================================================
    # Confidence columns
    # =====================================================

    confidence_columns = [

        "left_shoulder_confidence",
        "right_shoulder_confidence",

        "left_elbow_confidence",
        "right_elbow_confidence",

        "left_hip_confidence",
        "right_hip_confidence"
    ]


    results = []


    # =====================================================
    # EACH REP
    # =====================================================

    for _, rep in reps_df.iterrows():

        rep_number = int(
            rep["rep"]
        )


        # Convert 1-based frames -> 0-based
        start = (
            int(
                rep["start_frame"]
            )
            - 1
        )

        pull = (
            int(
                rep["pull_frame"]
            )
            - 1
        )

        end = (
            int(
                rep["end_frame"]
            )
            - 1
        )


        incomplete = bool(
            rep[
                "incomplete_return"
            ]
        )


        # -----------------------------------------
        # Check bounds
        # -----------------------------------------

        start = max(
            0,
            start
        )

        pull = min(
            pull,
            len(pose_df) - 1
        )

        end = min(
            end,
            len(pose_df) - 1
        )


        if (
            pull <= start
            or end <= pull
        ):
            continue


        # =================================================
        # Timing
        # =================================================

        pull_time = (

            pull - start

        ) / fps


        return_time = (

            end - pull

        ) / fps


        total_time = (

            end - start

        ) / fps


        # =================================================
        # ROM
        # =================================================

        start_window = (

            movement_signal[
                start:
                min(
                    start + 5,
                    len(
                        movement_signal
                    )
                )
            ]
        )


        pull_window = (

            movement_signal[
                max(
                    0,
                    pull - 2
                ):
                min(
                    pull + 3,
                    len(
                        movement_signal
                    )
                )
            ]
        )


        released_value = float(
            np.nanmean(
                start_window
            )
        )

        pulled_value = float(
            np.nanmean(
                pull_window
            )
        )


        rom_proxy = (

            pulled_value
            -
            released_value
        )


        # =================================================
        # Pull phase
        # =================================================

        pull_slice = slice(
            start,
            pull + 1
        )


        pull_shoulder_mean = float(
            np.nanmean(
                shoulder_asymmetry[
                    pull_slice
                ]
            )
        )


        pull_shoulder_max = float(
            np.nanmax(
                shoulder_asymmetry[
                    pull_slice
                ]
            )
        )


        pull_torso_mean = float(
            np.nanmean(
                torso_shift[
                    pull_slice
                ]
            )
        )


        pull_torso_max = float(
            np.nanmax(
                torso_shift[
                    pull_slice
                ]
            )
        )


        pull_elbow_mean = float(
            np.nanmean(
                elbow_asymmetry[
                    pull_slice
                ]
            )
        )


        pull_elbow_max = float(
            np.nanmax(
                elbow_asymmetry[
                    pull_slice
                ]
            )
        )


        pull_pose_quality = float(

            pose_df.iloc[
                start:
                pull + 1
            ][
                confidence_columns
            ]
            .mean()
            .mean()
        )


        # =================================================
        # Return phase
        # =================================================

        if incomplete:

            return_shoulder_mean = np.nan
            return_shoulder_max = np.nan

            return_torso_mean = np.nan
            return_torso_max = np.nan

            return_elbow_mean = np.nan
            return_elbow_max = np.nan

            return_pose_quality = np.nan


        else:

            return_slice = slice(
                pull,
                end + 1
            )


            return_shoulder_mean = float(
                np.nanmean(
                    shoulder_asymmetry[
                        return_slice
                    ]
                )
            )


            return_shoulder_max = float(
                np.nanmax(
                    shoulder_asymmetry[
                        return_slice
                    ]
                )
            )


            return_torso_mean = float(
                np.nanmean(
                    torso_shift[
                        return_slice
                    ]
                )
            )


            return_torso_max = float(
                np.nanmax(
                    torso_shift[
                        return_slice
                    ]
                )
            )


            return_elbow_mean = float(
                np.nanmean(
                    elbow_asymmetry[
                        return_slice
                    ]
                )
            )


            return_elbow_max = float(
                np.nanmax(
                    elbow_asymmetry[
                        return_slice
                    ]
                )
            )


            return_pose_quality = float(

                pose_df.iloc[
                    pull:
                    end + 1
                ][
                    confidence_columns
                ]
                .mean()
                .mean()
            )


        # =================================================
        # Save
        # =================================================

        results.append({

            "video":
                video_name,

            "label":
                label,

            "rep":
                rep_number,

            "start_frame":
                start + 1,

            "pull_frame":
                pull + 1,

            "end_frame":
                end + 1,

            "incomplete_return":
                incomplete,


            # Timing
            "pull_time_sec":
                pull_time,

            "return_time_sec":
                (
                    np.nan
                    if incomplete
                    else return_time
                ),

            "total_time_sec":
                total_time,


            # ROM
            "rom_proxy":
                rom_proxy,


            # Pull
            "pull_shoulder_asym_mean":
                pull_shoulder_mean,

            "pull_shoulder_asym_max":
                pull_shoulder_max,

            "pull_torso_shift_mean":
                pull_torso_mean,

            "pull_torso_shift_max":
                pull_torso_max,

            "pull_elbow_asym_mean":
                pull_elbow_mean,

            "pull_elbow_asym_max":
                pull_elbow_max,

            "pull_pose_quality":
                pull_pose_quality,


            # Return
            "return_shoulder_asym_mean":
                return_shoulder_mean,

            "return_shoulder_asym_max":
                return_shoulder_max,

            "return_torso_shift_mean":
                return_torso_mean,

            "return_torso_shift_max":
                return_torso_max,

            "return_elbow_asym_mean":
                return_elbow_mean,

            "return_elbow_asym_max":
                return_elbow_max,

            "return_pose_quality":
                return_pose_quality
        })


    return pd.DataFrame(
        results
    )


# =========================================================
# 12. PROCESS ONE VIDEO
# =========================================================

def process_video(
    video_path,
    label
):

    print()
    print(
        "================================================"
    )

    print(
        f"VIDEO: {video_path.name}"
    )

    print(
        f"LABEL: {label}"
    )

    print(
        "================================================"
    )


    # -----------------------------------------------------
    # Pose
    # -----------------------------------------------------

    pose_df, fps = extract_pose(
        video_path
    )


    pose_df = clean_pose_data(
        pose_df
    )


    # -----------------------------------------------------
    # Save keypoints
    # -----------------------------------------------------

    video_output_dir = (

        OUTPUT_DIR
        /
        video_path.stem
    )


    video_output_dir.mkdir(
        parents=True,
        exist_ok=True
    )


    keypoints_path = (

        video_output_dir
        /
        "keypoints.csv"
    )


    pose_df.to_csv(
        keypoints_path,
        index=False
    )


    # -----------------------------------------------------
    # Movement
    # -----------------------------------------------------

    movement_signal = (
        create_movement_signal(
            pose_df
        )
    )


    # -----------------------------------------------------
    # Segment reps
    # -----------------------------------------------------

    (
        reps_df,
        low_threshold,
        high_threshold

    ) = segment_reps(
        movement_signal
    )


    if len(reps_df) == 0:

        print(
            "WARNING: No repetitions detected."
        )

        return pd.DataFrame()


    reps_path = (

        video_output_dir
        /
        "reps.csv"
    )


    reps_df.to_csv(
        reps_path,
        index=False
    )


    # -----------------------------------------------------
    # Features
    # -----------------------------------------------------

    features_df = extract_form_features(

        pose_df=pose_df,

        reps_df=reps_df,

        movement_signal=movement_signal,

        fps=fps,

        video_name=video_path.name,

        label=label
    )


    features_path = (

        video_output_dir
        /
        "features.csv"
    )


    features_df.to_csv(
        features_path,
        index=False
    )


    print()
    print(
        f"FPS: {fps:.2f}"
    )

    print(
        f"Frames: {len(pose_df)}"
    )

    print(
        f"LOW threshold: "
        f"{low_threshold:.4f}"
    )

    print(
        f"HIGH threshold: "
        f"{high_threshold:.4f}"
    )

    print(
        f"Detected reps: "
        f"{len(reps_df)}"
    )


    complete_reps = (

        ~features_df[
            "incomplete_return"
        ]

    ).sum()


    print(
        f"Complete reps: "
        f"{complete_reps}"
    )


    return features_df


# =========================================================
# 13. BUILD FULL DATASET
# =========================================================

all_features = []


for (
    label,
    folder
) in CLASSES.items():


    if not folder.exists():

        print(
            f"Folder missing: "
            f"{folder}"
        )

        continue


    videos = sorted(
        folder.glob(
            "*.mp4"
        )
    )


    print()
    print(
        "################################################"
    )

    print(
        f"CLASS: {label.upper()}"
    )

    print(
        f"Videos found: {len(videos)}"
    )

    print(
        "################################################"
    )


    for video_path in videos:

        video_features = process_video(
            video_path,
            label
        )


        if not video_features.empty:

            all_features.append(
                video_features
            )


# =========================================================
# 14. COMBINE DATASET
# =========================================================

if len(all_features) == 0:

    raise RuntimeError(
        "No dataset samples were created."
    )


dataset_df = pd.concat(
    all_features,
    ignore_index=True
)


# =========================================================
# 15. Add Training Eligibility
# =========================================================
#
# We do not want to train on:
#
# - incomplete repetitions
# - extremely low-confidence pose data
#
# =========================================================

dataset_df[
    "usable_for_training"
] = (

    (
        dataset_df[
            "incomplete_return"
        ]
        ==
        False
    )

    &

    (
        dataset_df[
            "pull_pose_quality"
        ]
        >=
        0.70
    )
)


# =========================================================
# 16. SAVE FINAL DATASET
# =========================================================

dataset_df.to_csv(
    FINAL_DATASET_CSV,
    index=False
)


# =========================================================
# 17. SUMMARY
# =========================================================

print()
print()
print(
    "================================================"
)

print(
    "       DATASET BUILD COMPLETE"
)

print(
    "================================================"
)

print()


print(
    f"Total repetitions: "
    f"{len(dataset_df)}"
)


print()
print(
    "Samples per class:"
)

print(
    dataset_df[
        "label"
    ]
    .value_counts()
)


print()
print(
    "Usable training samples:"
)

print(

    dataset_df[

        dataset_df[
            "usable_for_training"
        ]

    ][
        "label"
    ]

    .value_counts()
)


print()
print(
    "Final Dataset:"
)

print(
    FINAL_DATASET_CSV
)

print()
print(
    "================================================"
)