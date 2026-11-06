"""Small YuNet/BlazeFace-style face detector: person crop resized to 160x160,
3 levels, objectness + box + 5 landmarks (eyes, nose, mouth corners) for alignment."""
from .detector import AnchorFreeDetector, DetectorConfig


def face_config(width: float = 1.0, input_size: int = 160) -> DetectorConfig:
    return DetectorConfig(name="face_det", input_size=input_size, width=width, neck=32, n_kpt=5, feat_dim=0,
                          stem=16, stages=[(16, 1, 2, 2), (24, 2, 2, 3), (40, 2, 2, 3), (64, 2, 2, 3)])


def build_face_detector(width: float = 1.0, input_size: int = 160) -> AnchorFreeDetector:
    return AnchorFreeDetector(face_config(width, input_size))
