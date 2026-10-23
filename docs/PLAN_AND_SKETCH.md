# Plan & Sketch

A plan is the step before a solid. The assistant does the research, draws a rough dimensioned sheet, and writes down what it assumed and what it still needs you to answer. You look at that sheet, answer, and either send it back or approve it. The expensive model starts after you approve. The assistant cannot approve for you.

The order is research, a dimensioned 2D sheet, assumptions and open questions, a debate, then a model. A plan sheet is that kind of picture, made of blocks, stored in the project as data.

## What you end up with

A plan has a status:

- **Draft.** The assistant is still adding notes and sheets.
- **In review.** The assistant has asked you to look. The sheets stay put until someone opens the next revision.
- **Approved.** You accepted this revision. Modeling and simulation may start from these numbers.

Revisions stay. v1 is kept when v2 is opened. Each revision says what changed.

The file is `plans/<name>.json` in the project, plus a PNG for each sheet. Opening the project does not run that file. It is not a script. Nothing in it is executed.

## Who does what

The assistant can start a plan, add research (every note needs a source), draw sheets, add assumptions and questions, comment, submit for review, and open a new revision.

You answer questions, accept or reject assumptions, waive a question, and approve. You can do that in the Plan panel, or with the commands below. The assistant's tools refuse those three actions and say so. There is no flag on those commands that lets the assistant through.

Approval is remembered on your computer, in `~/.config/aicad/plan-approvals.json`, not only as a word in the project file. Changing `plans/<name>.json` so it says approved does not open modeling. Undo of an approval removes the record of that approval. Redo puts the record back. A project you received from someone else stays closed until you approve it yourself, even when its file already says approved. Answers typed into the file the same way are shown as not entered on this computer. Opening the project does not rewrite the file.

The project tree can show a short plan status. The full review stays on the Plan page. On a workspace page that already has an empty `#plan-body`, the one line that fills it is:

```html
<script src="/static/plan.js"></script>
```

Two commands release the later work:

- `start_model_from_plan` releases heavy modeling.
- `start_simulation_from_plan` releases simulation.

Until you approve the current revision, both refuse. They do not build a solid and they do not run a solver. After approval they return the plan's dimensions and assumptions, which is what the modeling step should use. A new revision clears that approval.

Other modeling commands are unchanged. A bracket you were already building is not blocked by a draft plan. The gate is starting that heavy work *from the plan*.

## The shelf example

`examples/plan_shelf.json` is one plan for a wall shelf. It is data. It does not run.

The research cites the usual 305 mm wall-cabinet depth, 18 mm sheet in the 32 mm cabinet system, and the fact that a real shelf span still needs a plywood table this plan does not run. The sheet is two views, top and side. Yellow blocks are the arms. The blue block is the board. The white block is the wall plate. Gold lines are the key dimensions: 480 mm along the wall, 280 mm deep, 320 mm clear between the arms, 140 mm to the underside, 18 mm stock.

Two assumptions are still open: studs at 400 mm, and a 12 kg spread load. Two questions are open: 400 mm or 600 mm studs, and light things or a row of books.

Load it into a project:

```bash
aicad start-plan shelf --file examples/plan_shelf.json --workspace plan_demo
```

The title comes from the file. Ask to see it before anyone approves:

```bash
aicad start-model-from-plan shelf --workspace plan_demo
```

That command refuses. The sentence says the plan is still a draft, that modeling stays off, and that nothing was modeled. Simulation says the same kind of thing.

Open the app on that project:

```bash
aicad app --workspace plan_demo
```

Click **Plan**. The sheet is the blue blueprint. The right side is the research, the assumptions, and the questions.

## A second revision

Suppose you answer that the studs are 600 mm apart. The 480 mm plate was drawn for 400 mm centers, so the assistant should not quietly overwrite v1. It opens v2 and says what changed:

```bash
aicad revise-plan shelf --workspace plan_demo \
  --changed "Studs are 600 mm, from your answer. The plate went from 480 mm to 640 mm."
```

v1 is still there, including its sheet. The new revision is a draft again. If v1 had been approved, that approval no longer covers v2, and modeling stays off until you approve v2.

## Commands

The app, the command line, and the assistant's connection all call the same operations. On the command line the names use hyphens. The assistant's tools use underscores (`start_plan`, `put_plan_sheet`, and the rest).

| Command | Who |
| --- | --- |
| `start-plan`, `add-plan-research`, `add-plan-assumption`, `add-plan-question`, `put-plan-sheet`, `set-plan-dimensions` | Assistant or you, while the revision is a draft |
| `comment-on-plan`, `submit-plan`, `revise-plan` | Either. Revise records what changed and opens the next draft |
| `answer-plan-question`, `mark-plan-item`, `approve-plan` | You only |
| `start-model-from-plan`, `start-simulation-from-plan` | Either, and only after you have approved the current revision |
| `list-plans`, `get-plan` | Either |

A sheet is a JSON object. Blocks are rectangles in millimeters: `x`, `y`, `w`, `h`, `label`, and a `fill` (`yellow`, `blue`, `green`, `red`, `white`, `door`, `shelter`, or `leg`). A dimension is the two points it measures, the text, and an `offset` so the line sits off the block. `panels` puts more than one view on the sheet. Units are millimeters.

`mark-plan-item` takes `accepted`, `rejected`, or `open` for an assumption, and `waived` or `open` for a question.

## Hands-on test

Do this yourself in the app, the way you would if someone else had drawn the shelf.

1. From the repo, load the example and open the app:

   ```bash
   aicad start-plan shelf --file examples/plan_shelf.json --workspace plan_demo
   aicad app --workspace plan_demo
   ```

2. Click **Plan**. You should see **Wall shelf bracket**, status **draft**, revision **v1**, and a blue sheet with a top view and a side view. Gold dimensions read 480 mm, 280 mm, 320 mm clear, 140 mm, and 18 mm. The banner says modeling is closed.

3. Read the three research notes and check that each one names a source. Read the two assumptions and the two questions.

4. On question `q1`, type `600 mm apart` and click **Answer**. On assumption `a2` (the 12 kg load), click **Accept**. Leave `q2` open if you want to see the warning later, or answer it.

5. Click **Try modeling**. The panel should show the refusal: modeling stays off, the plan is still a draft, and nothing was modeled. **Try simulation** should refuse the same way.

6. Click **Submit for review**. The status becomes **in review**.

7. Click **Approve** and confirm. The status becomes **approved**. The banner says modeling is open. The dimensions table is still the 480, 400, 280, 18, and 140 mm figures.

8. Click **Try modeling** again. This time it should release the plan and list those dimensions. It still does not create a solid. That is the handoff. The solid is a later step, using these numbers.

9. Optional, back in the terminal, so you can see the same gate the assistant hits:

   ```bash
   aicad start-model-from-plan shelf --workspace plan_demo
   ```

   After step 7 this prints the dimensions. If you run it before step 7, it refuses.

What you are checking: you can see the sheet, answer in the app, and the model stays off until the approval is yours.
