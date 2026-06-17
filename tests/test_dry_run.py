import inspect
import subprocess
import sys

from engine.orchestrator import run_pipeline


def test_run_pipeline_signature_has_dry_run():
    params = inspect.signature(run_pipeline).parameters
    assert "dry_run" in params, (
        "run_pipeline() must accept a dry_run keyword argument"
    )


def test_cli_parser_has_dry_run_flag():
    result = subprocess.run(
        [sys.executable, "-m", "engine.orchestrator", "--help"],
        capture_output=True,
        text=True,
    )
    assert "--dry-run" in result.stdout, (
        "--dry-run flag must appear in CLI help output"
    )
