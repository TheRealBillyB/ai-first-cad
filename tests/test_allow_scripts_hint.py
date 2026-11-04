"""The allow command names the project as ".", so the hint says where to run it."""

from aicad.sandbox import design_script_status, warn_design_scripts


def test_the_enable_command_says_where_to_run_it(tmp_path, capsys):
    status = design_script_status(tmp_path)
    assert status["enable"].endswith("--workspace .")
    assert "project folder" in status["enable_from"]
    assert str(tmp_path.resolve()) not in status["enable_from"]
    warn_design_scripts(tmp_path)
    assert status["enable_from"] in capsys.readouterr().err
