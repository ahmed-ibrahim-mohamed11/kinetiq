from pathlib import Path

import cv2
import pandas as pd


# =========================================================
# Paths
# =========================================================

AI_DIR = Path(__file__).resolve().parent.parent

DATASET_DIR = AI_DIR / "data" / "seated_row"

OUTPUT_CSV = (
    AI_DIR
    / "outputs"
    / "manual_rep_annotations.csv"
)


# =========================================================
# Videos
# =========================================================

VIDEOS = [

    (
        DATASET_DIR / "correct" / "correct_01.mp4",
        "correct"
    ),

    (
        DATASET_DIR / "correct" / "correct_02.mp4",
        "correct"
    ),

    (
        DATASET_DIR / "correct" / "correct_03.mp4",
        "correct"
    ),

    (
        DATASET_DIR / "asymmetry" / "asymmetry_01.mp4",
        "asymmetry"
    ),

    (
        DATASET_DIR / "asymmetry" / "asymmetry_02.mp4",
        "asymmetry"
    ),

    (
        DATASET_DIR / "asymmetry" / "asymmetry_03.mp4",
        "asymmetry"
    ),
]


REPS_PER_VIDEO = 5


# =========================================================
# Storage
# =========================================================

records = []


# =========================================================
# Window
# =========================================================

WINDOW = "Manual Rep Annotation"

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
# Process Videos
# =========================================================

for video_path, label in VIDEOS:

    if not video_path.exists():

        print(
            f"Missing: {video_path}"
        )

        continue


    cap = cv2.VideoCapture(
        str(video_path)
    )


    fps = cap.get(
        cv2.CAP_PROP_FPS
    )

    total_frames = int(
        cap.get(
            cv2.CAP_PROP_FRAME_COUNT
        )
    )


    frame_index = 0

    paused = True

    rep_number = 1

    marker_stage = 0


    start_frame = None
    pull_frame = None


    print()
    print("==============================")
    print(f"VIDEO: {video_path.name}")
    print(f"LABEL: {label}")
    print("==============================")
    print()
    print("SPACE = Play / Pause")
    print("M     = Mark")
    print("A     = Previous frame")
    print("D     = Next frame")
    print("Q     = Quit")
    print()


    while rep_number <= REPS_PER_VIDEO:


        # ---------------------------------------------
        # Read frame
        # ---------------------------------------------

        cap.set(
            cv2.CAP_PROP_POS_FRAMES,
            frame_index
        )


        success, frame = cap.read()


        if not success:
            break


        current_frame = frame_index + 1


        # ---------------------------------------------
        # Instruction
        # ---------------------------------------------

        if marker_stage == 0:

            instruction = (
                "Press M at START"
            )

        elif marker_stage == 1:

            instruction = (
                "Press M at MAX PULL"
            )

        else:

            instruction = (
                "Press M at END"
            )


        # ---------------------------------------------
        # Overlay
        # ---------------------------------------------

        cv2.rectangle(
            frame,
            (20, 20),
            (760, 250),
            (0, 0, 0),
            -1
        )


        cv2.putText(
            frame,
            f"VIDEO: {video_path.name}",
            (40, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2
        )


        cv2.putText(
            frame,
            f"REP: {rep_number}/5",
            (40, 100),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (255, 255, 255),
            2
        )


        cv2.putText(
            frame,
            f"FRAME: {current_frame}/{total_frames}",
            (40, 140),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2
        )


        cv2.putText(
            frame,
            instruction,
            (40, 190),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 255),
            2
        )


        status = (
            "PAUSED"
            if paused
            else
            "PLAYING"
        )


        cv2.putText(
            frame,
            f"STATUS: {status}",
            (40, 230),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2
        )


        cv2.imshow(
            WINDOW,
            frame
        )


        # ---------------------------------------------
        # Keyboard
        # ---------------------------------------------

        delay = (
            30
            if paused
            else max(
                int(1000 / fps),
                1
            )
        )


        key = (
            cv2.waitKey(delay)
            & 0xFF
        )


        # SPACE
        if key == ord(" "):

            paused = not paused


        # Previous frame
        elif key == ord("a"):

            paused = True

            frame_index = max(
                frame_index - 1,
                0
            )


        # Next frame
        elif key == ord("d"):

            paused = True

            frame_index = min(
                frame_index + 1,
                total_frames - 1
            )


        # MARK
        elif key == ord("m"):

            paused = True


            # START
            if marker_stage == 0:

                start_frame = (
                    current_frame
                )

                marker_stage = 1

                print(
                    f"REP {rep_number} "
                    f"START = {start_frame}"
                )


            # PULL
            elif marker_stage == 1:

                pull_frame = (
                    current_frame
                )

                marker_stage = 2

                print(
                    f"REP {rep_number} "
                    f"PULL = {pull_frame}"
                )


            # END
            elif marker_stage == 2:

                end_frame = (
                    current_frame
                )


                records.append({

                    "video":
                        video_path.name,

                    "label":
                        label,

                    "rep":
                        rep_number,

                    "start_frame":
                        start_frame,

                    "pull_frame":
                        pull_frame,

                    "end_frame":
                        end_frame
                })


                print()
                print(
                    f"REP {rep_number} SAVED ✅"
                )

                print(
                    f"{start_frame} "
                    f"→ {pull_frame} "
                    f"→ {end_frame}"
                )

                print()


                rep_number += 1

                marker_stage = 0

                start_frame = None

                pull_frame = None


        # Quit
        elif key == ord("q"):

            pd.DataFrame(
                records
            ).to_csv(
                OUTPUT_CSV,
                index=False
            )

            cap.release()

            cv2.destroyAllWindows()

            print(
                f"Saved to: {OUTPUT_CSV}"
            )

            raise SystemExit


        # ---------------------------------------------
        # Playback
        # ---------------------------------------------

        if not paused:

            frame_index += 1


            if frame_index >= total_frames:

                frame_index = (
                    total_frames - 1
                )

                paused = True


    cap.release()


# =========================================================
# Save
# =========================================================

annotations = pd.DataFrame(
    records
)

annotations.to_csv(
    OUTPUT_CSV,
    index=False
)


cv2.destroyAllWindows()


print()
print("==============================")
print("ANNOTATION COMPLETE")
print("==============================")

print(
    f"Total reps: {len(annotations)}"
)

print(
    f"Saved to:\n{OUTPUT_CSV}"
)