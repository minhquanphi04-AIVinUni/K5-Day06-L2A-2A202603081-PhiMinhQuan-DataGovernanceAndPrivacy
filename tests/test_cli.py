"""Lab contract: one-command run, offline guard, GT guard, evaluate + compare (T08)."""
import json
import socket
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from src import compare, evaluate
from src import main as runner
from src.common.gt_guard import GroundTruthAccessError, disable_gt_guard, enable_gt_guard
from src.common.offline import NetworkBlockedError, disable_offline_guard, enable_offline_guard
from src.metrics.tracks import TRACKS


def test_poc_pipeline_runs_end_to_end(tmp_path):
    # The group pipeline (src/pipeline.py) is implemented, so a plain run must succeed...
    rc = runner.main(["--track", "t08", "--output", str(tmp_path), "--offline", "--seed", "42"])
    assert rc == 0
    info = json.loads((tmp_path / "run_info.json").read_text())
    assert info["status"] == 0 and info["seed"] == 42
    # output contract: actions.csv with the t08 pred_columns
    pred = pd.read_csv(tmp_path / "actions.csv")
    assert {"request_id", "artefact_id", "action"} <= set(pred.columns)
    assert not pred.duplicated(["request_id", "artefact_id"]).any()   # _unique contract
    # ...while an unimplemented pipeline module still fails loudly with exit code 3.
    import sys, types
    stub = types.ModuleType("src.stub_unimplemented")
    def _run(ctx):
        raise NotImplementedError("stub: process() not implemented")
    stub.run = _run
    sys.modules["src.stub_unimplemented"] = stub
    try:
        rc3 = runner.main(["--track", "t08", "--pipeline", "src.stub_unimplemented",
                           "--output", str(tmp_path / "stub"), "--offline"])
    finally:
        sys.modules.pop("src.stub_unimplemented", None)
    assert rc3 == 3                                  # NotImplemented -> explicit exit code
    info3 = json.loads((tmp_path / "stub" / "run_info.json").read_text())
    assert info3["status"] == 3


def _run_baseline(out):
    return runner.main(["--track", "t08", "--pipeline", "src.baseline", "--output", str(out),
                        "--offline", "--seed", "42", "--run-name", "baseline"])


def test_baseline_end_to_end(tmp_path):
    out = tmp_path / "baseline"
    assert _run_baseline(out) == 0
    for f in ["actions.csv", "review_queue.csv", "contact_sheet.html", "decision.json",
              "decision_log.csv", "audit_log.jsonl", "run_info.json"]:
        assert (out / f).exists(), f
    base = pd.read_csv(out / "actions.csv")
    assert {"request_id", "artefact_id", "action"} <= set(base.columns)
    # same seed -> identical output (reproducibility / idempotency)
    out2 = tmp_path / "baseline2"
    _run_baseline(out2)
    assert (out / "actions.csv").read_text() == (out2 / "actions.csv").read_text()


def test_poc_is_deterministic_two_runs_byte_identical(tmp_path):
    # T08.md protocol: "Idempotency: chạy 2 lần phải ra cùng kết quả"
    outs = []
    for i in (1, 2):
        o = tmp_path / f"poc{i}"
        assert runner.main(["--track", "t08", "--output", str(o), "--offline", "--seed", "42"]) == 0
        outs.append(o)
    assert (outs[0] / "actions.csv").read_text() == (outs[1] / "actions.csv").read_text()


def test_evaluate_without_ground_truth_fails_clearly(tmp_path, capsys):
    pred = tmp_path / "actions.csv"
    pred.write_text("request_id,artefact_id,action\nDR-001,a0001,delete\n")
    gt = tmp_path / "missing.csv"
    # use the t08 default GT path shape (fixtures/t08_deletion/gt/gt_actions.csv, instructor-only)
    assert evaluate.main(["--track", "t08", "--gt", str(gt), "--pred", str(pred)]) == 2
    assert "not distributed" in capsys.readouterr().err


@pytest.mark.requires_gt
def test_poc_evaluate(tmp_path):
    out = tmp_path / "poc"
    assert runner.main(["--track", "t08", "--output", str(out), "--offline", "--seed", "42"]) == 0
    mb = tmp_path / "metrics_poc.json"
    assert evaluate.main(["--track", "t08", "--pred", str(out / "actions.csv"), "--run-name", "poc",
                          "--out", str(mb)]) == 0
    payload = json.loads(mb.read_text())
    prim = [m for m in payload["metrics"] if m.primary][0]
    assert prim["name"] == "Deletion Completeness" and payload["seed"] == 42
    md = tmp_path / "t1.md"
    assert compare.main([str(mb), str(mb), "--out", str(md)]) == 0
    assert "Deletion Completeness" in md.read_text()


def test_missing_input_folder_exit_2(tmp_path):
    assert runner.main(["--track", "t08", "--input", str(tmp_path / "nope"), "--output", str(tmp_path)]) == 2


def test_offline_guard_blocks_network():
    enable_offline_guard()
    try:
        with pytest.raises(NetworkBlockedError):
            socket.create_connection(("huggingface.co", 443), timeout=1)
    finally:
        disable_offline_guard()


def test_gt_guard_blocks_ground_truth_reads(tmp_path):
    enable_gt_guard()
    try:
        # GT raises even though the file does not exist in the student repo: the guard
        # fires on the *path* (fixtures/.../gt/...), before any file access.
        gt_path = Path("fixtures") / TRACKS["t08"].fixture_dir / TRACKS["t08"].default_gt
        assert "gt" in gt_path.parts and not gt_path.exists()
        with pytest.raises(GroundTruthAccessError):
            pd.read_csv(gt_path)
        pd.read_csv(Path("fixtures") / TRACKS["t08"].fixture_dir / "manifest.csv")   # inputs are fine
    finally:
        disable_gt_guard()


def test_cli_module_invocation():
    r = subprocess.run([sys.executable, "-m", "src.evaluate", "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "--pred" in r.stdout


def test_track_inferred_from_input_folder(tmp_path):
    # contract command without --track: track comes from the fixture folder name
    rc = runner.main(["--input", "fixtures/t08_deletion", "--output", str(tmp_path), "--offline", "--seed", "42"])
    assert rc == 0 and (tmp_path / "actions.csv").exists()
