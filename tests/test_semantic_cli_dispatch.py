"""The public CLI must route a real Nav2 request to the real runner."""

from unittest.mock import patch

from scripts.go2w import run_semantic_exploration as runner


def test_nav2_cli_dispatches_to_real_runner() -> None:
    with (
        patch.object(runner, "run_go2w", return_value=7) as real,
        patch.object(runner, "_run_offline", return_value=8) as offline,
    ):
        assert runner.main(["--target", "chair", "--backend", "nav2"]) == 7
    real.assert_called_once()
    assert real.call_args.args[0].backend == "nav2"
    offline.assert_not_called()
