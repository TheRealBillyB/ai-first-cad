---
date: 2026-11-08
---

# Model and Assembly are one workspace with one 3D view

The Assembly page was buttons, a picture, and reports. Its only live 3D view was Orbit, which quietly loaded the assembly into the Model view with nothing to say so, and a click there was sent as a face of the selected part, so every click reported a face reference error. Model showed each part once where it was modeled, which looked like a broken assembly. Model and Assembly now share one workspace and one 3D view, and the page buttons switch between them. Assembly shows the placed instances in their colors. A click picks an instance and sends nothing, and Edit part opens its part in Model. A joint with limits has a slider, and the view moves when a joint changes without moving the camera. A joint a relation drives names its driver instead of offering a control that would do nothing. Fixed joints are folded away. The assembly tools and their reports sit beside the view. Editing a part in place inside the assembly is a later feature.
