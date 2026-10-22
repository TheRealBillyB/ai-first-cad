"""Report a part's volume in cubic centimeters.

This package is a third-party plugin. aicad does not import it when a design
opens, and it does not import it until you allow this package.
"""


def register(host) -> None:
    host.add_analysis(
        "volume",
        "Convert the part volume from cubic millimeters to cubic centimeters.",
        volume_cm3,
    )


def volume_cm3(part, options) -> dict:
    del options
    cm3 = part.volume_mm3 / 1000.0
    return {
        "part": part.name,
        "volume_mm3": part.volume_mm3,
        "volume_cm3": round(cm3, 3),
        "note": (
            f"{part.name} occupies {cm3:.3f} cm³. "
            "This is the solid's volume, not a mass, and it is not a built-in check."
        ),
    }
