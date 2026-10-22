# Write your first plugin

A plugin is a small package someone else can install. It can add an analysis, a part library, or an exporter. It does not edit aicad, and it does not replace a built-in check, the standard-part catalog, or STEP and STL export.

This is a door, not a framework. Version 1 has one call, `register(host)`, and three ways to add something. `aicad.plugins.API_VERSION` is `1`.

## What will not happen

- Opening a design does not load a plugin. A plugin is not stored in the design.
- Installing a plugin does not run it. aicad can list it first.
- aicad reads `aicad.plugin.json` as text. It does not import the plugin to learn the publisher or the permissions.
- The warning is written by aicad. The plugin does not get a hook to replace it.

## The example

The example reports a part's volume in cubic centimeters. The files are in `examples/hello_plugin`.

### 1. Declare the plugin

`examples/hello_plugin/src/hello_plugin/aicad.plugin.json`:

```json
{
  "api": 1,
  "name": "volume-note",
  "publisher": "aicad example",
  "summary": "Reports one part's volume in cubic centimeters.",
  "permissions": ["checks"],
  "provides": ["analysis"]
}
```

`api` must be `1`. `name` must match the entry point below. `provides` is one or more of `analysis`, `parts`, and `exporter`.

`permissions` is the list the warning shows. Version 1 knows three:

| Permission | What the publisher is declaring |
| --- | --- |
| `checks` | It will run an analysis on a part you name. An analysis must declare this. |
| `project_files` | It wants the project folder during a call you make. |
| `network` | It says it will use the network. |

aicad does not open a network connection for a plugin, and it does not block one. The list is what you are agreeing to. It is not a sandbox. After you allow the package, its Python runs in the aicad process.

A part library or an exporter may declare no extra permissions. It still runs only after you allow it, and only when you ask.

### 2. Point an entry point at it

In the plugin's `pyproject.toml`:

```toml
[project.entry-points."aicad.plugins"]
volume-note = "hello_plugin:register"
```

The group `aicad.plugins` is how aicad finds installed plugins without importing them. Version 1 allows one plugin per package, and the entry point has to be a top-level module (`hello_plugin:register`, not `hello_plugin.extra:register`). Put `aicad.plugin.json` in that module's directory, next to its code. A normal install also needs the file listed as package data, which the example does under `[tool.setuptools.package-data]`. An editable install is found from that same file.

### 3. Register one analysis

```python
def register(host) -> None:
    host.add_analysis(
        "volume",
        "Convert the part volume from cubic millimeters to cubic centimeters.",
        volume_cm3,
    )


def volume_cm3(part, options) -> dict:
    cm3 = part.volume_mm3 / 1000.0
    return {
        "part": part.name,
        "volume_cm3": round(cm3, 3),
        "note": f"{part.name} occupies {cm3:.3f} cm³. This is volume, not a mass.",
    }
```

`part` is a snapshot: `name`, `volume_mm3`, `area_mm2`, `center_mm`, `size_mm`, `valid`, and `watertight`. For an analysis, `step_file` is `None`. `workspace` is the project folder only if you declared `project_files`. The same value is on `host.workspace`, and it is `None` while `register` itself is running.

Return a dict. aicad wraps it and says the report came from the plugin, not from a built-in check. Putting `"passed": true` inside your dict does not turn it into a built-in check.

While `register` runs, the top of the module runs too. Allowing a plugin allows that code, not only the function you named.

### 4. Install, read the warning, allow, run

Install aicad first, then the example:

```bash
pip install -e examples/hello_plugin
aicad plugins list
aicad plugins show volume-note
aicad plugins allow volume-note
aicad plugins allow volume-note --yes
```

`allow` without `--yes` prints the warning and does not allow anything. It also does not import the plugin. `--yes` records consent for this package version, this entry point, and this declaration. It still does not import the plugin.

Make a part, then run the analysis:

```bash
aicad run examples/shaft.py --name shaft
aicad plugins run volume-note volume shaft
```

Any small solid works. A `10 × 20 × 30` mm box is `6` cm³.

The allow is stored in user config: `$XDG_CONFIG_HOME/aicad/plugins.json`, or `~/.config/aicad/plugins.json`. It is not stored in the design. Set `AICAD_PLUGIN_CONSENT` to point at a different file.

If the package version, the entry point, or `aicad.plugin.json` changes, the old allow stops working and the warning comes back. An editable install that changes the Python without changing that declaration is still the version you allowed. Read the code you install.

`aicad plugins revoke volume-note` forgets the allow.

## A part library

Declare `"provides": ["parts"]`. You do not need `checks` for this. `build` returns a script that assigns `part`. Options from `--set key=value` arrive as strings.

```python
def register(host) -> None:
    host.add_part("pin", "A square pin. Option: size, in millimeters.", build_pin)

def build_pin(options) -> str:
    size = float(options.get("size", "6"))
    return f"part = Box({size}, {size}, {size * 4})\n"
```

```bash
aicad plugins insert my-parts dowel --kind pin --set size=6
```

aicad saves that script as an ordinary part. The next time the design opens, aicad rebuilds the script. It does not import the plugin again. Part scripts saved in the design follow the session-open rule, which is separate from this door.

`host.workspace` during `build` is the project folder only if you declared `project_files`.

## An exporter

Declare `"provides": ["exporter"]`. Return text or bytes. aicad writes the file at the path the person gave. Do not use the format names `step` or `stl`.

```python
def register(host) -> None:
    host.add_exporter("note", "Write the part volume as a text file.", write_note)

def write_note(part, options) -> str:
    return f"{part.name} {part.volume_mm3} mm^3\n"
```

```bash
aicad plugins export my-notes box --format note --out box-volume.txt
```

For an export, `part.step_file` is a temporary copy of the solid, valid only while `write_note` runs. Read it there if you need the geometry. It is not the part in the design, and deleting or editing that temp file does not change the model. The copy is removed when the call returns.

## From Python

```python
import aicad.plugins as plugins

offer = plugins.get("volume-note")
print(plugins.warning_text(offer))
plugins.allow("volume-note", accept=True)
```

Show the warning before `accept=True`. `accept` has to be the boolean `True`. `load` imports the module. Prefer `run_analysis`, `insert_part`, and `export_with`, which load the plugin only after consent and mark the result as coming from the plugin.

`discover()` lists what is installed and does not import any of it.

## Version 1

A plugin whose `api` is not `1` is listed and refused. It is not imported. A later version may add optional fields that old plugins can ignore. A change that breaks an existing plugin bumps `API_VERSION`.

The host methods are `add_analysis(name, summary, run)`, `add_part(kind, summary, build)`, and `add_exporter(format, summary, write)`. Names are short and lowercase, such as `volume` or `volume-note`.

## License

aicad is AGPL-3.0-or-later. A plugin that is loaded into aicad, or that imports aicad, is combined with this program. If you distribute that plugin, the AGPL-3.0-or-later obligations apply to it as well. You keep your copyright. There is no contributor license agreement and no relicensing grant. The example plugin is AGPL-3.0-or-later for that reason.

A plugin that never imports aicad is still loaded into the aicad process when you run it. Treat that as linking the core.

## What this door does not do

It does not sandbox the plugin. It does not add materials, and it does not add a domain pack for marine, electronics, or buildings. It does not run because a file was opened. Built-in checks, the fastener catalog, and STEP and STL export stay in aicad.
