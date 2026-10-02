from pathlib import Path

import cv2
import pandas as pd


# =========================================================
# 1. Paths
# =========================================================

AI_DIR = Path(__file__).resolve().parent.parent.parent

VIDEO_PATH = (
    AI_DIR
    / "data"
    / "seated_row"
    / "asymmetry"
    / "asymmetry_01.mp4"
)

CSV_PATH = (
    AI_DIR
    / "outputs"
    / "manual_rep_annotations.csv"
)


# =========================================================
# 2. Target Annotation
# =========================================================

TARGET_VIDEO = "asymmetry_01.mp4"
TARGET_REP = 3

START_FRAME = 111
PULL_FRAME = 126


# =========================================================
# 3. Check Files
# =========================================================

if not VIDEO_PATH.exists():
    raise FileNotFoundError(
        f"Video not found:\n{VIDEO_PATH}"
    )

if not CSV_PATH.exists():
    raise FileNotFoundError(
        f"CSV not found:\n{CSV_PATH}"
    )


# =========================================================
# 4. Load CSV
# =========================================================

df = pd.read_csv(CSV_PATH)


mask = (
    (df["video"] == TARGET_VIDEO)
    &
    (df["rep"] == TARGET_REP)
)


if mask.sum() != 1:

    raise RuntimeError(
        f"Expected exactly one row for "
        f"{TARGET_VIDEO} Rep {TARGET_REP}, "
        f"but found {mask.sum()}."
    )


current_end = int(
    df.loc[
        mask,
        "end_frame"
    ].iloc[0]
)


print()
print("Current annotation:")
print(f"Video : {TARGET_VIDEO}")
print(f"Rep   : {TARGET_REP}")
print(f"Start : {START_FRAME}")
print(f"Pull  : {PULL_FRAME}")
print(f"End   : {current_end}")


# =========================================================
# 5. Open Video
# =========================================================

cap = cv2.VideoCapture(
    str(VIDEO_PATH)
)


if not cap.isOpened():
    raise RuntimeError(
        "Could not open video."
    )


total_frames = int(
    cap.get(
        cv2.CAP_PROP_FRAME_COUNT
    )
)


# Start directly at pull frame
frame_index = PULL_FRAME - 1


# =========================================================
# 6. Window
# =========================================================

WINDOW = "Fix Annotation - Rep 3"

cv2.namedWindow(
    WINDOW,
    cv2.WINDOW_NORMAL
)

cv2.resizeWindow(
    WINDOW,
    960,
    540
)


# =========================================================
# 7. Instructions
# =========================================================

print()
print("==============================")
print("Controls")
print("==============================")
print("D = Next frame")
print("A = Previous frame")
print("F = Forward 5 frames")
print("B = Back 5 frames")
print("E = Save this frame as END")
print("Q = Quit without changing")
print("==============================")
print()


# =========================================================
# 8. Frame Loop
# =========================================================

while True:

    frame_index = max(
        PULL_FRAME - 1,
        min(
            frame_index,
            total_frames - 1
        )
    )


    cap.set(
        cv2.CAP_PROP_POS_FRAMES,
        frame_index
    )


    success, frame = cap.read()


    if not success:
        break


    current_frame = frame_index + 1


    # -----------------------------------------------------
    # Overlay
    # -----------------------------------------------------

    cv2.rectangle(
        frame,
        (20, 20),
        (760, 240),
        (0, 0, 0),
        -1
    )


    cv2.putText(
        frame,
        "Fix asymmetry_01 - Rep 3",
        (40, 60),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (255, 255, 255),
        2
    )


    cv2.putText(
        frame,
        f"START: {START_FRAME}",
        (40, 105),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2
    )


    cv2.putText(
        frame,
        f"PULL: {PULL_FRAME}",
        (40, 145),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2
    )


    cv2.putText(
        frame,
        f"CURRENT FRAME: {current_frame}",
        (40, 185),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 255),
        2
    )


    cv2.putText(
        frame,
        "Press E when athlete reaches END position",
        (40, 225),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2
    )


    cv2.imshow(
        WINDOW,
        frame
    )


    key = (
        cv2.waitKey(0)
        & 0xFF
    )


    # -----------------------------------------------------
    # Next frame
    # -----------------------------------------------------

    if key == ord("d"):

        frame_index += 1


    # -----------------------------------------------------
    # Previous frame
    # -----------------------------------------------------

    elif key == ord("a"):

        frame_index -= 1


    # -----------------------------------------------------
    # Forward 5
    # -----------------------------------------------------

    elif key == ord("f"):

        frame_index += 5


    # -----------------------------------------------------
    # Back 5
    # -----------------------------------------------------

    elif key == ord("b"):

        frame_index -= 5


    # -----------------------------------------------------
    # Save END
    # -----------------------------------------------------

    elif key == ord("e"):

        new_end = current_frame


        if new_end <= PULL_FRAME:

            print(
                "END must be after PULL."
            )

            continue


        print()
        print(
            f"New END selected: {new_end}"
        )


        confirm = input(
            "Save this value? (y/n): "
        ).strip().lower()


        if confirm == "y":

            df.loc[
                mask,
                "end_frame"
            ] = new_end


            df.to_csv(
                CSV_PATH,
                index=False
            )


            print()
            print("==============================")
            print("Annotation updated successfully.")
            print("==============================")
            print(f"Start: {START_FRAME}")
            print(f"Pull : {PULL_FRAME}")
            print(f"End  : {new_end}")
            print()
            print(f"Saved to:\n{CSV_PATH}")

            break


        else:

            print(
                "Not saved. Continue reviewing."
            )


    # -----------------------------------------------------
    # Quit
    # -----------------------------------------------------

    elif key == ord("q"):

        print(
            "Exited without changing CSV."
        )

        break


# =========================================================
# 9. Cleanup
# =========================================================

cap.release()

cv2.destroyAllWindows()