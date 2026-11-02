---
date: 2026-11-02
---

# Tendon wraps stay continuous through a study

`check_cables` solved each sample on its own, so a sheave's wrap was always the arc in its wrap direction plus the authored `turns`. A line that starts with one full turn and unwinds past it, such as a crossed loop on an idler as the joint flexes, jumped from just over one turn to almost two between neighbouring samples, and its length jumped by one circumference. `turns` now applies to the first sample. Each later sample keeps the whole turns nearest the wrap at the sample before, never below zero, so a line that unwinds or winds past a full turn stays continuous.
