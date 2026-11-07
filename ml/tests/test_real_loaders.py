"""Real-dataset hooks, exercised on tiny fake annotation files in each format."""
import json

import numpy as np
from PIL import Image

from sentinel_ml.data import real


def _img(path, w=200, h=100):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.full((h, w, 3), 128, np.uint8)).save(path)


def test_letterbox():
    img, s, (px, py) = real.letterbox(np.zeros((100, 200, 3), np.uint8), 256)
    assert img.shape == (256, 256, 3) and s == 256 / 200 and px == 0 and py == 64


def test_coco_person(tmp_path):
    _img(tmp_path / "val2017" / "a.jpg")
    _img(tmp_path / "val2017" / "b.jpg")
    (tmp_path / "annotations").mkdir()
    ann = {"images": [{"id": 1, "file_name": "a.jpg"}, {"id": 2, "file_name": "b.jpg"}],
           "annotations": [{"image_id": 1, "category_id": 1, "bbox": [10, 10, 20, 50], "iscrowd": 0},
                           {"image_id": 1, "category_id": 3, "bbox": [0, 0, 5, 5], "iscrowd": 0},
                           {"image_id": 1, "category_id": 1, "bbox": [0, 0, 90, 90], "iscrowd": 1}]}
    (tmp_path / "annotations" / "instances_val2017.json").write_text(json.dumps(ann))
    items = real.coco_person_items(str(tmp_path), "val")
    assert len(items) == 2 and items[0][1] == [[10, 10, 30, 60]] and items[1][1] == []
    ds = real.BoxDataset(items, 256, train=False)
    x, t = ds[0]
    assert x.shape == (3, 256, 256)
    np.testing.assert_allclose(t["boxes"].numpy(), [[12.8, 76.8, 38.4, 140.8]], atol=1e-4)


def test_crowdhuman(tmp_path):
    _img(tmp_path / "Images" / "x1.jpg")
    rec = {"ID": "x1", "gtboxes": [{"tag": "person", "fbox": [1, 2, 30, 60]},
                                   {"tag": "mask", "fbox": [0, 0, 5, 5]},
                                   {"tag": "person", "fbox": [5, 5, 5, 5], "extra": {"ignore": 1}}]}
    (tmp_path / "annotation_val.odgt").write_text(json.dumps(rec) + "\n")
    items = real.crowdhuman_items(str(tmp_path), "val")
    assert items[0][1] == [[1, 2, 31, 62]]


def test_wider_face_with_landmarks(tmp_path):
    _img(tmp_path / "WIDER_train" / "images" / "0--P" / "a.jpg")
    (tmp_path / "wider_face_split").mkdir()
    (tmp_path / "wider_face_split" / "wider_face_train_bbx_gt.txt").write_text(
        "0--P/a.jpg\n2\n10 10 20 20 0 0 0 0 0 0\n50 5 10 12 0 0 0 1 0 0\n")
    lm = tmp_path / "label.txt"
    lm.write_text("# 0--P/a.jpg\n10 10 20 20 14 15 0 24 15 0 20 20 0 15 25 0 25 25 0 1\n")
    items = real.wider_face_items(str(tmp_path), "train", str(lm))
    assert items[0][1] == [[10, 10, 30, 30]]  # the invalid face is dropped
    assert np.array(items[0][2]).shape == (1, 5, 2)
    x, t = real.BoxDataset(items, 160, n_kpt=5, train=True)[0]
    assert t["kpts"].shape == (1, 10)


def test_faceid_folder(tmp_path):
    for who in ("alice", "bob"):
        for i in range(2):
            _img(tmp_path / who / f"{i}.png", 112, 112)
    ds = real.FaceIdFolder(str(tmp_path))
    assert len(ds) == 4 and list(ds.labels) == [0, 0, 1, 1]
    assert ds[0][0].shape == (3, 112, 112)
