from .angles import SIGNAL_EXTRACTORS, extract_signal
from .base import COCO_KEYPOINTS, Keypoint, Pose, PoseEstimator

__all__ = ["COCO_KEYPOINTS", "Keypoint", "Pose", "PoseEstimator", "SIGNAL_EXTRACTORS", "extract_signal"]

# Adapters (edge_ai.pose.onnx_pose.YoloPoseOnnx, edge_ai.pose.rtmpose.RtmPoseOnnx) and
# edge_ai.pose.factory.create_pose_estimator are imported from their modules on purpose:
# they pull in the inference stack, which unit tests of the pure-Python parts do not need.
