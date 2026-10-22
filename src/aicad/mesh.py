"""Prepare a solid that Gmsh refused, without changing the caller's model.

Healing sews gaps and fixes tolerances. Defeaturing drops faces that are
too small to mesh. Both report what they changed, and how far the volume
moved, so a later solve can refuse to call the result a pass.
"""

from __future__ import annotations

from aicad import kernel
from aicad.errors import GeometryError


def volume_mm3(shape: kernel.Shape) -> float:
    return float(shape.impl.volume)


def face_count(shape: kernel.Shape) -> int:
    return len(shape.impl.faces())


def heal(shape: kernel.Shape, tolerance_mm: float) -> tuple[kernel.Shape, str, bool]:
    """Sew and fix the solid. ``changed`` is true when the volume moved.

    A build123d solid is often wrapped in a compound. Sewing that compound
    returns a shell. The shell is turned back into a solid. If that fails,
    the original solid is kept so a later mesh can still run.
    """
    before = volume_mm3(shape)
    try:
        healed = _cast(_heal_topo(shape.impl.wrapped, tolerance_mm), shape.features)
    except Exception as exc:
        detail = " ".join(str(exc).split())[:200] or exc.__class__.__name__
        return (
            shape,
            f"Healing did not produce a solid ({detail}). The original solid was kept.",
            False,
        )
    after = volume_mm3(healed)
    moved = _moved(before, after)
    change = " The volume changed." if moved else " The volume was unchanged."
    note = (
        f"Sewed and fixed the solid with a {tolerance_mm:g} mm tolerance, "
        f"which can close gaps and drop edges shorter than that.{change}"
    )
    return healed, note, moved


def defeature(shape: kernel.Shape, min_face_mm2: float) -> tuple[kernel.Shape, str, int]:
    """Remove faces smaller than ``min_face_mm2``. Returns how many were removed."""
    small = _small_faces(shape, min_face_mm2)
    if not small:
        return shape, f"No face was smaller than {min_face_mm2:g} mm², so nothing was removed.", 0
    try:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
    except ImportError:
        healed, note, _moved_volume = heal(shape, max(0.05, min_face_mm2**0.5))
        return (
            healed,
            "This OpenCascade build has no face-removal tool. " + note,
            0,
        )
    tool = BRepAlgoAPI_Defeaturing()
    tool.SetShape(shape.impl.wrapped)
    for face in small:
        tool.AddFaceToRemove(face)
    tool.Build()
    if not tool.IsDone():
        raise GeometryError("Removing the small faces did not finish.")
    simplified = _cast(tool.Shape(), shape.features)
    before = volume_mm3(shape)
    after = volume_mm3(simplified)
    moved = " The volume changed." if _moved(before, after) else " The volume was unchanged."
    note = f"Removed {len(small)} faces smaller than {min_face_mm2:g} mm².{moved}"
    return simplified, note, len(small)


def _small_faces(shape: kernel.Shape, min_face_mm2: float) -> list:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    found = []
    explorer = TopExp_Explorer(shape.impl.wrapped, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        if float(props.Mass()) < min_face_mm2:
            found.append(face)
        explorer.Next()
    return found


def _heal_topo(wrapped, tolerance_mm: float):
    """Fix, sew, and return one solid. Sewing a compound otherwise yields a shell."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
    from OCP.ShapeFix import ShapeFix_Shape
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopoDS import TopoDS, TopoDS_Compound

    solids = _explore(wrapped, TopAbs_SOLID, TopoDS.Solid)
    source = solids[0] if len(solids) == 1 else wrapped
    fixer = ShapeFix_Shape(source)
    try:
        fixer.SetPrecision(float(tolerance_mm))
        fixer.SetMaxTolerance(float(tolerance_mm) * 10.0)
    except Exception:
        pass
    fixer.Perform()
    sewing = BRepBuilderAPI_Sewing(float(tolerance_mm))
    sewing.Add(fixer.Shape())
    sewing.Perform()
    sewn = sewing.SewedShape()
    try:
        unify = ShapeUpgrade_UnifySameDomain(sewn, True, True, False)
        unify.Build()
        sewn = unify.Shape()
    except Exception:
        pass
    made = _solids_of(sewn)
    if len(made) == 1:
        return made[0]
    if len(made) > 1:
        compound = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(compound)
        for solid in made:
            builder.Add(compound, solid)
        return compound
    raise GeometryError("The sewed shape has no solid.")


def _solids_of(sewn) -> list:
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
    from OCP.TopAbs import TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopoDS import TopoDS

    solids = _explore(sewn, TopAbs_SOLID, TopoDS.Solid)
    if solids:
        return solids
    made = []
    for shell in _explore(sewn, TopAbs_SHELL, TopoDS.Shell):
        builder = BRepBuilderAPI_MakeSolid(shell)
        if builder.IsDone():
            made.append(builder.Solid())
    if not made and sewn.ShapeType() == TopAbs_SHELL:
        builder = BRepBuilderAPI_MakeSolid(TopoDS.Shell(sewn))
        if builder.IsDone():
            made.append(builder.Solid())
    return made


def _explore(wrapped, kind, cast):
    from OCP.TopExp import TopExp_Explorer

    found = []
    explorer = TopExp_Explorer(wrapped, kind)
    while explorer.More():
        found.append(cast(explorer.Current()))
        explorer.Next()
    return found


def _cast(topo, features) -> kernel.Shape:
    from build123d import Shape as BuildShape

    cast = BuildShape.cast(topo)
    if len(cast.solids()) == 0 or float(cast.volume) <= 0:
        raise GeometryError("The simplified solid has no volume.")
    return kernel.Shape(cast, features=features)


def _moved(before: float, after: float) -> bool:
    if before <= 1e-9:
        return abs(after - before) > 1e-6
    return abs(after - before) / before > 0.001
