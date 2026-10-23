# Connect an assistant in two minutes

aicad speaks MCP, the same kind of connection Cursor and Claude Desktop use for local tools. The assistant and the app edit one project. Units are millimeters.

You need `aicad` installed (`pip install -e ".[dev]"` from this repo, or `pip install` once a release exists) and a project folder. The folder can be empty. aicad creates `session.json` in it.

## Cursor

Open Cursor settings, then MCP, or edit `~/.cursor/mcp.json`. Use the full path to `aicad` and the full path to the project folder.

```json
{
  "mcpServers": {
    "aicad": {
      "command": "/full/path/to/.venv/bin/aicad",
      "args": ["serve", "--workspace", "/full/path/to/your-project"]
    }
  }
}
```

On Windows the command is `C:\\full\\path\\to\\.venv\\Scripts\\aicad.exe`. Restart the MCP server in Cursor after you save the file. The tool list should include `list_parts` and `inspect_part`.

## Claude Desktop

Claude Desktop reads `claude_desktop_config.json`. On a Mac that file is in `~/Library/Application Support/Claude/`. On Windows it is in `%APPDATA%\\Claude\\`.

```json
{
  "mcpServers": {
    "aicad": {
      "command": "/full/path/to/.venv/bin/aicad",
      "args": ["serve", "--workspace", "/full/path/to/your-project"]
    }
  }
}
```

Quit Claude Desktop and open it again. The aicad tools show up in the tool list for a new chat.

## Any other MCP client

The server uses standard input and output. It does not open a port. Point the client at the `aicad` program with the argument `serve`, and pass the project folder:

```bash
aicad serve --workspace /full/path/to/your-project
```

A client that only speaks HTTP or server-sent events cannot attach yet. That transport is not in this version. Stdio is the connection this version supports.

## Same window as the app

Start the app with the `aicad` command. Do not run `python -m aicad` from a project folder. For development, use `python -P -m aicad`.

Start the app first:

```bash
aicad app --workspace /full/path/to/your-project
```

The browser opens at `http://127.0.0.1:8765`. Then point the assistant at that app instead of a second copy of the files:

```bash
aicad serve --connect http://127.0.0.1:8765
```

Use that as the MCP command. Leave out `--workspace`. One process owns the project, so the picture in the browser updates when the assistant edits.

That connection is the assistant, not the person. The app labels it that way, and the assistant cannot relabel itself by putting a `source` field in the command. Another website cannot send commands as you either. A program with a shell on this same computer still can: it can read the page and act as you. An approval step in front of the assistant is a guardrail, not a boundary against that program.

## What the assistant can do

The tools match the `aicad` command line and the buttons in the app: create and edit parts and assemblies, run the checks, render pictures, and export STEP, STL, URDF, and MJCF. It can also read the project (`get_state`, `session_payload`, and the `aicad://` resources), save, open another folder, and undo.

`run_design_script` is the way to hand the assistant one Python program that calls several steps. It is off until you allow it. The assistant cannot allow it.

## Allow a design script

A design script is code the assistant wrote. It can change files inside the project folder. It cannot use the network, and it cannot read or write files outside that folder. Opening the project does not run it.

Allow it once per project, yourself:

```bash
aicad allow-scripts --workspace /full/path/to/your-project
```

Or open the app and click **Allow design scripts** on the banner. The banner stays up so you can see that scripts are on. Each run is appended to `.aicad/script_log.jsonl` in the project.

The network stays off. To let every design script in this app process use the network, start the app with `--script-network`. To allow one run, click **Allow network on the next script**. There is no MCP tool for either switch.

Part scripts, the ones that only assign `part`, are how a single solid is made. They run in the same confined process. They do not need the allow step, and they are not written to the design-script log.

## If it does not connect

- The command has to be the `aicad` from the environment where aicad is installed. A system `python` that has not installed the package will not work.
- `--workspace` has to be a folder, and the path has to be absolute. A relative path depends on whichever directory the assistant started in.
- The warning about design scripts is printed on the error stream. The data stream is only the MCP protocol. If a client reports garbage on the data stream, something else is printing there.
