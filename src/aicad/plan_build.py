"""Run the scripts stored on an approved plan.

The caller checks the approval receipt before this runs. Nothing here
approves a plan, and the network is not a parameter: part scripts stay off
the network. Each script runs in its own directory, not in the project, so
it cannot read or change the project files. The project's time limit still
applies.
"""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path

from aicad.errors import GeometryError, PlanBuildError, ScriptError


def run_build_steps(workspace: str | Path, steps: list[dict], on_progress) -> tuple[list, list]:
    """Run each step. Return solids and progress. A failure saves nothing."""
    progress: list[dict] = []
    built: list[tuple[str, str, object]] = []
    count = len(steps)
    project = Path(workspace)
    for index, step in enumerate(steps, start=1):
        row = {
            "id": step["id"],
            "part": step["part"],
            "index": index,
            "count": count,
            "status": "running",
        }
        progress.append(row)
        on_progress(copy.deepcopy(progress))
        try:
            shape = _run_step(step["script"], project)
        except (ScriptError, GeometryError) as exc:
            row["status"] = "failed"
            row["error"] = _detail(exc)
            for later_index, later in enumerate(steps[index:], start=index + 1):
                progress.append(
                    {
                        "id": later["id"],
                        "part": later["part"],
                        "index": later_index,
                        "count": count,
                        "status": "not_run",
                    }
                )
            snapshot = copy.deepcopy(progress)
            on_progress(snapshot)
            raise PlanBuildError(
                (
                    f"Build step {step['id']!r} ({index} of {count}), part {step['part']!r}, failed: "
                    f"{row['error']} "
                    "The previous model was left in place. Nothing from this build was saved."
                ),
                step_id=step["id"],
                part=step["part"],
                index=index,
                count=count,
                progress=snapshot,
            ) from exc
        row["status"] = "passed"
        on_progress(copy.deepcopy(progress))
        built.append((step["part"], step["script"], shape))
    return built, progress


def _run_step(script: str, project: Path):
    """Run one part script outside the project and return its solid.

    The sandbox root is a new directory. The project's time limit is the
    limit for that run, and it cannot be raised past the cap.
    """
    from aicad.sandbox import run_part_script

    with tempfile.TemporaryDirectory(prefix="aicad-plan-") as folder:
        return run_part_script(script, folder, wall_from=project)


def _detail(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    if not text:
        text = exc.__class__.__name__
    if len(text) > 500:
        return text[:500] + "..."
    return text
