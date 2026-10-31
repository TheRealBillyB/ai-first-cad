"""Run the scripts stored on an approved plan.

The caller checks the approval receipt before this runs. Nothing here
approves a plan, and the network is not a parameter: part scripts stay off
the network.
"""

from __future__ import annotations

import copy
from pathlib import Path

from aicad.errors import GeometryError, PlanBuildError, ScriptError


def run_build_steps(workspace: str | Path, steps: list[dict], on_progress) -> tuple[list, list]:
    """Run each step. Return solids and progress. A failure saves nothing."""
    from aicad.kernel import make_from_script

    progress: list[dict] = []
    built: list[tuple[str, str, object]] = []
    count = len(steps)
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
            shape = make_from_script(step["script"], workspace=workspace)
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


def _detail(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    if not text:
        text = exc.__class__.__name__
    if len(text) > 500:
        return text[:500] + "..."
    return text
