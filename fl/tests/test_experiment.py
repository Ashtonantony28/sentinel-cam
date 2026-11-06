import csv

from sentinel_fl.experiment import grid, main


def test_quick_experiment_writes_results(tmp_path):
    main(["--quick", "--workers", "2", "--out", str(tmp_path)])
    md = (tmp_path / "results.md").read_text()
    assert md.startswith("SIMULATED")
    labels = [c.label() for c in grid(20, 0.1)]
    for lab in labels:
        assert f"| {lab} |" in md
    rows = list(csv.DictReader(open(tmp_path / "per_home.csv")))
    assert len(rows) == 2 * 60 * len(labels)
    assert {"false_alert_rate", "person_recall"} <= set(rows[0])
    assert (tmp_path / "results.json").exists()
