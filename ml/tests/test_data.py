import numpy as np

from sentinel_ml.data.synthetic_face import (TEMPLATE_112, FaceIdentity, SyntheticFaceDetDataset,
                                             SyntheticFaceIdDataset, render_aligned_face, render_face_scene)
from sentinel_ml.data.synthetic_person import SyntheticPersonDataset, render_person_scene


def test_person_scene_deterministic_and_valid():
    a, ba = render_person_scene(5)
    b, bb = render_person_scene(5)
    np.testing.assert_array_equal(a, b)
    np.testing.assert_array_equal(ba, bb)
    assert a.shape == (256, 256, 3) and a.dtype == np.float32 and 0 <= a.min() and a.max() <= 1
    counts = [len(render_person_scene(i)[1]) for i in range(40)]
    assert 0 in counts and max(counts) >= 2  # negatives and multi-person scenes both occur
    for i in range(40):
        bx = render_person_scene(i)[1]
        assert np.all(bx[:, 2] > bx[:, 0]) and np.all(bx[:, 3] > bx[:, 1])
        assert np.all(bx >= 0) and np.all(bx <= 256)


def test_person_dataset_flip_keeps_boxes_valid():
    ds = SyntheticPersonDataset(8, seed=3, size=128)
    for i in range(len(ds)):
        x, t = ds[i]
        assert x.shape == (3, 128, 128)
        b = t["boxes"].numpy()
        assert np.all(b[:, 2] >= b[:, 0]) and np.all(b[:, 0] >= 0) and np.all(b[:, 2] <= 128)


def test_face_identity_stable_and_distinct():
    a, b = FaceIdentity.from_seed(1), FaceIdentity.from_seed(1)
    assert a.eye_dx == b.eye_dx and np.all(a.skin == b.skin)
    assert FaceIdentity.from_seed(2).eye_dx != a.eye_dx
    # same identity, different nuisance → closer (pixel space) than a different identity, on average
    same = [np.abs(render_aligned_face(7, s) - render_aligned_face(7, s + 100)).mean() for s in range(6)]
    diff = [np.abs(render_aligned_face(7, s) - render_aligned_face(8 + s, s + 100)).mean() for s in range(6)]
    assert np.mean(same) < np.mean(diff)


def test_face_scene_landmarks_inside_boxes():
    for i in range(30):
        img, boxes, kpts = render_face_scene(i)
        assert img.shape == (160, 160, 3)
        assert kpts.shape == (len(boxes), 5, 2)
        for b, k in zip(boxes, kpts):
            w = b[2] - b[0]
            # eyes above mouth corners, landmarks near the box (box was clipped to the image)
            assert k[0, 1] < k[3, 1] and k[1, 1] < k[4, 1] or abs(k[0, 1] - k[3, 1]) < w


def test_face_datasets():
    d = SyntheticFaceDetDataset(6, seed=1)
    x, t = d[0]
    assert x.shape == (3, 160, 160) and t["kpts"].shape[1] == 10
    ids = SyntheticFaceIdDataset(3, 2, id_offset=50)
    assert len(ids) == 6 and list(ids.labels) == [0, 0, 1, 1, 2, 2]
    assert ids[0][0].shape == (3, 112, 112)
    assert TEMPLATE_112.shape == (5, 2)
