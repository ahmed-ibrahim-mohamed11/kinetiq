from pathlib import Path
import csv

import cv2
from ultralytics import YOLO


# =========================================================
# 1. Project Paths
# =========================================================

AI_DIR = Path(__file__).resolve().parent.parent.parent

VIDEO_PATH = (
    AI_DIR
    / "data"
    / "raw"
    / "seated_row_test.mp4"
)

OUTPUT_DIR = AI_DIR / "outputs"

OUTPUT_VIDEO = (
    OUTPUT_DIR
    / "seated_row_pose.mp4"
)

OUTPUT_CSV = (
    OUTPUT_DIR
    / "seated_row_keypoints.csv"
)

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# =========================================================
# 2. Check Input Video
# =========================================================

if not VIDEO_PATH.exists():
    raise FileNotFoundError(
        f"\nVideo not found:\n{VIDEO_PATH}\n"
    )


# =========================================================
# 3. Load YOLO Pose Model
# =========================================================

print("Loading YOLO Pose model...")

model = YOLO(
    "yolo11n-pose.pt"
)

print("Model loaded successfully.")


# =========================================================
# 4. Important joints for Seated Row
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
# 5. Open Input Video
# =========================================================

cap = cv2.VideoCapture(
    str(VIDEO_PATH)
)

if not cap.isOpened():
    raise RuntimeError(
        "Could not open input video."
    )


fps = cap.get(
    cv2.CAP_PROP_FPS
)

width = int(
    cap.get(
        cv2.CAP_PROP_FRAME_WIDTH
    )
)

height = int(
    cap.get(
        cv2.CAP_PROP_FRAME_HEIGHT
    )
)

total_frames = int(
    cap.get(
        cv2.CAP_PROP_FRAME_COUNT
    )
)


# Safety fallback
if fps <= 0:
    fps = 30


print()
print("==============================")
print("Video Information")
print("==============================")

print(f"FPS: {fps:.2f}")
print(f"Resolution: {width} x {height}")
print(f"Total Frames: {total_frames}")

print("==============================")
print()


# =========================================================
# 6. Create Output Video
# =========================================================

fourcc = cv2.VideoWriter_fourcc(
    *"mp4v"
)

writer = cv2.VideoWriter(
    str(OUTPUT_VIDEO),
    fourcc,
    fps,
    (width, height)
)

if not writer.isOpened():
    raise RuntimeError(
        "Could not create output video."
    )


# =========================================================
# 7. Create CSV File
# =========================================================

csv_file = open(
    OUTPUT_CSV,
    "w",
    newline="",
    encoding="utf-8"
)

csv_writer = csv.writer(
    csv_file
)


# CSV header
header = [
    "frame"
]

for joint_name in JOINTS:

    header.extend(
        [
            f"{joint_name}_x",
            f"{joint_name}_y",
            f"{joint_name}_confidence"
        ]
    )

csv_writer.writerow(
    header
)


# =========================================================
# 8. Display Window Settings
# =========================================================

WINDOW_NAME = (
    "GYM AI - Seated Row"
)

cv2.namedWindow(
    WINDOW_NAME,
    cv2.WINDOW_NORMAL
)

cv2.resizeWindow(
    WINDOW_NAME,
    960,
    540
)


# =========================================================
# 9. Process Video
# =========================================================

frame_number = 0


while True:

    # -----------------------------------------------------
    # Read Frame
    # -----------------------------------------------------

    success, frame = cap.read()

    if not success:
        break


    frame_number += 1


    # -----------------------------------------------------
    # Run Pose Estimation
    # -----------------------------------------------------

    results = model(
        frame,
        verbose=False
    )

    result = results[0]


    # Start CSV row
    row = [
        frame_number
    ]


    # -----------------------------------------------------
    # Extract Pose Keypoints
    # -----------------------------------------------------

    if (
        result.keypoints is not None
        and len(result.keypoints) > 0
    ):

        keypoints = (
            result
            .keypoints
            .data
            .cpu()
            .numpy()
        )


        number_of_people = len(
            keypoints
        )


        print(
            f"Frame {frame_number} "
            f"| People: {number_of_people}"
        )


        # ---------------------------------------------
        # MVP:
        # Use the first detected person
        # ---------------------------------------------

        person = keypoints[0]


        # ---------------------------------------------
        # Read important joints
        # ---------------------------------------------

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


            # Save to CSV
            row.extend(
                [
                    x,
                    y,
                    confidence
                ]
            )


            # Warn when confidence is low
            if confidence < 0.5:

                print(
                    f"   Low confidence -> "
                    f"{joint_name}: "
                    f"{confidence:.2f}"
                )


    else:

        print(
            f"Frame {frame_number} "
            f"| No person detected"
        )


        # Add empty data
        for _ in JOINTS:

            row.extend(
                [
                    "",
                    "",
                    ""
                ]
            )


    # -----------------------------------------------------
    # Save Keypoints to CSV
    # -----------------------------------------------------

    csv_writer.writerow(
        row
    )


    # -----------------------------------------------------
    # Draw YOLO Skeleton
    # -----------------------------------------------------

    annotated_frame = (
        result.plot()
    )


    # -----------------------------------------------------
    # Save original-resolution output
    # -----------------------------------------------------

    writer.write(
        annotated_frame
    )


    # -----------------------------------------------------
    # Show smaller display window
    # -----------------------------------------------------

    cv2.imshow(
        WINDOW_NAME,
        annotated_frame
    )


    # -----------------------------------------------------
    # Press Q to Stop
    # -----------------------------------------------------

    key = (
        cv2.waitKey(1)
        & 0xFF
    )

    if key == ord("q"):
        print()
        print("Stopped by user.")
        break


# =========================================================
# 10. Cleanup
# =========================================================

cap.release()

writer.release()

csv_file.close()

cv2.destroyAllWindows()


# =========================================================
# 11. Finished
# =========================================================

print()
print("========================================")
print("Pose Estimation Finished")
print("========================================")

print()
print("Output Video:")
print(OUTPUT_VIDEO)

print()
print("Keypoints CSV:")
print(OUTPUT_CSV)

print()
print("========================================")