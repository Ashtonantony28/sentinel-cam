"""Tiny single-class person detector, 256x256, < 1M params, plus a 64-d neck feature
for the false-alert head (INTERFACES.md §5)."""
from .detector import AnchorFreeDetector, DetectorConfig


def person_config(width: float = 1.0, input_size: int = 256) -> DetectorConfig:
    return DetectorConfig(name="person_det", input_size=input_size, width=width, neck=48, feat_dim=64,
                          stages=[(24, 2, 2, 3), (32, 3, 2, 3), (64, 3, 2, 3), (96, 2, 2, 3)])


def build_person_detector(width: float = 1.0, input_size: int = 256) -> AnchorFreeDetector:
    return AnchorFreeDetector(person_config(width, input_size))
