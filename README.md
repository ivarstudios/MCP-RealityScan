# MCP-RealityScan

An [MCP](https://modelcontextprotocol.io) server that drives **RealityScan 2.x** (formerly
RealityCapture) on Windows through its command-line interface. With it, Claude or any other MCP
client can align, mesh, simplify, unwrap, texture and export models, then read the results back
on the same PC.

RealityScan jobs often run for hours, and an MCP tool call cannot stay open that long. The server
therefore runs long work as background jobs with log files, and you check on them with status
calls. It can also send commands into a visible RealityScan window, so a person can watch the work
and take over.

> Not affiliated with or endorsed by Epic Games. RealityScan and RealityCapture are trademarks of
> Epic Games, Inc.

## Requirements

- Windows 10 or 11.
- **RealityScan 2.x**, installed from the Epic Games launcher. **Open it once in the GUI and log
  in** under the Windows user that will run the MCP server. Headless runs reuse that cached login,
  and without it they fail with unclear errors.
- **Python 3.10 or newer** on `PATH`. A per-user install from python.org is enough.
- An MCP client: the Claude desktop app, Claude Code, or another client that starts stdio servers.

## Install

```powershell
git clone https://github.com/ivarstudios/MCP-RealityScan.git
cd MCP-RealityScan
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Decide where the clone will live before you install. The `.venv` is tied to that path, so if you
move the folder later, delete `.venv` and run the installer again. Don't put the clone in a folder
that syncs between machines (OneDrive, Dropbox, Resilio and the like), because a synced venv points
at the other machine's Python.

`install.ps1` creates `.venv`, installs the package in editable mode, finds `RealityScan.exe`, runs
the self-test against a stub executable and writes `claude_desktop_config.generated.json` with the
paths filled in.

### Register with the Claude desktop app

1. Quit Claude fully from the tray icon. Closing the window is not enough.
2. Open `%APPDATA%\Claude\claude_desktop_config.json` and add the `RealityScan` entry from
   `claude_desktop_config.generated.json` under `mcpServers`. Edit the existing file rather than
   replacing it, because it also holds the app's own settings. Paths in JSON need doubled
   backslashes:

   ```json
   {
     "mcpServers": {
       "RealityScan": {
         "command": "C:\\path\\to\\MCP-RealityScan\\.venv\\Scripts\\python.exe",
         "args": ["-m", "realityscan_mcp"],
         "env": {
           "REALITYSCAN_EXE": "C:\\Program Files\\Epic Games\\RealityScan_2.2\\RealityScan.exe"
         }
       }
     }
   }
   ```

3. Start Claude. The server is listed as **RealityScan**.

### Register with Claude Code

```powershell
claude mcp add RealityScan --scope user `
  -e "REALITYSCAN_EXE=C:\Program Files\Epic Games\RealityScan_2.2\RealityScan.exe" `
  -- "C:\path\to\MCP-RealityScan\.venv\Scripts\python.exe" -m realityscan_mcp
claude mcp get RealityScan
```

`claude mcp get` should report **Connected**. To change a setting later, run
`claude mcp remove RealityScan -s user` and add the server again.

### Check it works

Ask Claude to run `check_connection` (executable, version, file roots, jobs folder) and then
`preflight`, which also checks that the roots exist, the jobs folder is writable, and whether
RealityScan is already running. To test the live-GUI mode, have it call `launch_gui` with an
instance name such as `RSTEST`. A RealityScan window should open, and `instance_status RSTEST`
should report `id:0xffffffff` (idle).

## Configuration

All settings are environment variables, set in the `env` block of the client config.

| variable | meaning |
|---|---|
| `REALITYSCAN_EXE` | Full path to `RealityScan.exe`. Without it, the server searches `C:\Program Files\Epic Games\RealityScan*` and takes the highest version. Set it if several versions are installed. |
| `REALITYSCAN_HOME` | Optional. Install folder that contains `RealityScan.exe`, as an alternative to `REALITYSCAN_EXE`. |
| `RS_MCP_ROOTS` | Optional. `;`-separated folders that the file tools may read and write. If unset, every local fixed drive except the system drive is allowed (see [Security](#security)). |
| `RS_MCP_JOBS` | Optional. Where each background job keeps its `argv.json`, `stdout.log`, `stderr.log` and `progress.txt`. Default: `%LOCALAPPDATA%\realityscan-mcp\jobs`. |
| `RS_MCP_DEFAULT_TIMEOUT` | Optional. Seconds a synchronous `rs_run` waits before the process is killed (default 3600). Background jobs have no timeout. |

## Tools

**Connection.** `check_connection` reports the executable, version, roots and jobs folder.
`preflight` also checks that the roots exist, the jobs folder is writable, and which RealityScan
processes are already running.

**Execution.** `rs_run(commands)` runs synchronously and is meant for quick things.
`rs_start_job(commands)` runs in the background and returns a job id. Follow up with
`rs_job_status`, `rs_job_wait(timeout)`, `rs_job_log`, `rs_jobs` and `rs_job_cancel`. `commands`
is an argv token list with one token per element and absolute paths. The server adds
`-headless -silent <crashdir> -stdConsole` at the start and `-quit` at the end. Every token that
starts with `-` is checked against a whitelist of 208 documented commands, because an unknown
command opens a modal dialog even in headless mode and hangs the process. To run a command that is
newer than the whitelist, pass `validate=false`.

**Live GUI (delegate mode).** `launch_gui(instance_name, project)` opens a visible, named
RealityScan window. Pass `instance=<name>` to `rs_run` or `rs_start_job` to send commands into that
window with `-delegateTo`, so someone can watch and take over. `instance_status` queries the window
with `-getStatus`.

**Project helpers.** `export_report(project, out, template)`,
`export_reconstruction_region(project, out.rsbox)`, `dump_global_settings(out.rcconfig)`,
`run_rscmd_file(path)` (runs a command file with `-execRSCMD`) and `list_known_commands(filter)`.

**Files** (inside the roots only). `fs_list`, `fs_find(patterns)`, `fs_info`, `fs_read(tail=)`,
`fs_write`, `xml_read`, `xml_patch(in, out, attributes=, elements=)` and
`xml_write(path, root_tag, attributes)`. These read and write the params, `.rsbox`, `.rsInfo` and
report files that the CLI depends on.

**Compose.** `compose_model_export(...)` builds the sequence
`-load → -selectModel|-selectMaximalComponent → [-setReconstructionRegion] → [-simplify] →
[-unwrap] → [-calculateTexture] → -exportSelectedModel <file> [params.xml] → [-save]` and returns
the token list so you can review it. `start=true` also runs it as a background job.

**Development.** `dev_reload` reloads the server's modules after you edit the source. A change to
the tool list in `server.py` still needs a client restart.

## Security

The server runs as your Windows user and acts on your files, so check that its reach suits the
machine before you use it.

- **File tools** (`fs_*`, `xml_*`) refuse relative paths and any path outside the roots. With
  `RS_MCP_ROOTS` unset, the roots are **every local fixed drive except the system drive, with read
  and write access**. `C:\` and network shares are excluded. On a shared machine, set
  `RS_MCP_ROOTS` to the project folders only. `fs_write` and `xml_write` don't overwrite
  existing files unless you pass `overwrite=true`, but `xml_patch` overwrites its output by
  default.
- **RealityScan itself is not confined to the roots.** Any path in a command, such as `-save`,
  `-export…` or `-execRSCMD`, goes to RealityScan unchanged. The whitelist only rejects unknown
  command names, and `validate=false` skips that check.
- No network listener is involved. The client starts the server as a stdio subprocess.

## How the CLI takes settings

Verified on RealityScan 2.2.0.119430.

Heavy commands accept an optional params file as their last argument: `-simplify`, `-unwrap`,
`-calculateTexture`, `-exportSelectedModel`, `-exportModel` and `-exportGroundControlPoints`.
Without one, they use the application's current settings.

There are two file formats. Which one a command wants depends on where the GUI saves it:

- **Tool settings panels** (Unwrap, Texture, Colour and Texture settings, and so on) save a
  `<Configuration id="{…}"><entry key="…" value="…"/></Configuration>` file. The `key` names are
  exactly the `-set "key=value"` names, for example `MvsDoCorrectColors`, `unwrapFixedTexelSize`
  and `txtImageDownscaleTexture`. To get one, set the panel up in the GUI, save it with the panel's
  save icon and read it with `xml_read`.
- **Export dialogs** save a single `<ModelExport …/>` element. RealityScan copies the same block
  into the `.rsInfo` it writes beside every export, so any earlier export's `.rsInfo` is a complete
  export-params reference. Change `settingsAnchor`, `exportVertexColors` and so on with `xml_patch`.
- **Reconstruction regions** are `.rsbox` files: a `<ReconstructionRegion>` with `yawPitchRoll`,
  `widthHeightDepth` and `CentreEuclid/centre` (UTM easting, northing and height when the project
  is georeferenced). `export_reconstruction_region` writes the project's current region, which
  you can then patch.
- `-exportGlobalSettings` writes a **binary** `.rcconfig` (a `TBES` container) that can't be read.
  To find key names, use the panel-saved Configuration files described above.

**Reports.** `-exportReport <out.html> <template.html>` works with the templates in
`<install>\Reports\`. `Overview.html` has components, models, triangle counts and reprojection
errors. `ComponentAccuracyReport.html` has the GCP table with residuals and RMSE; select the
component first. `SelectedModel.html` has texel size, texture count and utilisation, and timings.
The output HTML is large, so grep it or strip the tags.

## Delegate mode: what "done" means

With `instance=<name>`, a short-lived courier process passes every token after `-delegateTo` to
the named window, including any trailing `-quit`, which would close the window. For that reason
the server doesn't add `-quit` in delegate mode. To close the window on purpose, delegate
`["-quit"]`.

The courier returns as soon as it has handed over the batch, so a delegate run reported as `done`
means the batch was *delivered*, not that it *finished*. Poll `instance_status`: `id:0xffffffff`
means idle, and any other id is a running process with `progress`, `runtime` and `endEstimation`.

Only windows started by this server (`launch_gui`) can be reached. A RealityScan window opened
from the Start menu has no instance name, so `-getStatus` can't find it.

## Measured behaviours (RealityScan 2.2.0.119430)

These were observed while using the server on production photogrammetry projects. Other versions
may behave differently.

**Exports and coordinates**

- **`settingsAnchor` is added to RealityScan's internal local model coordinates.** It is not the
  point that becomes the origin. With anchor `0 0 0`, the `.rsInfo` `transformToModel` translation
  is the local origin O in the output CRS. To export `file = global − A`, set `anchor = O − A`. O
  differs between projects. If you set the anchor to a full UTM coordinate instead, the model ends
  up hundreds of kilometres out and float formats quantise it to about 0.5 m.
- **A non-zero `settingsRotation` combined with an anchor** produced coordinates in the millions,
  quantised. Keep the rotation at `0 0 0`. The file is then X east, Y north, Z up in the output
  CRS, and any axis swaps belong in the importer.
- `exportCoordinateSystemType="2"` means the **project's output coordinate system**. Set it first,
  for example `-setOutputCoordinateSystem epsg:32607`. Otherwise the export uses whatever the
  project already has, possibly EPSG:4326, which means degrees.
- `exportVertexColors="1"` means *no* vertex colours. `"0"` still writes a `vertexColors`
  attribute.
- `formatAndVersionUID` is `"abc 000 "` for Alembic and `"fbx 000 FBX202000"` for FBX, read from
  the `.rsInfo` of a default export. An FBX export with the Alembic UID silently writes nothing.
- An `authorComment` longer than about 120 characters makes the export fail silently, and
  `-getStatus` shows `lastError:-2147418113`.
- Exporting onto an existing file name overwrites the model and `.rsInfo`, but leaves behind any
  UDIM tiles from the earlier export that the new one doesn't write. Delete them first or export to
  a fresh name.
- Don't export at UTM magnitudes. Keep file coordinates small and record the origin.

**Models, regions and trimming**

- **Each model has its own reconstruction region.** `-setReconstructionRegion` applies to the
  *selected* model, so run `-selectModel <name>` before setting the region. If you set it first, it
  changes the wrong model and a later trim removes nothing.
- To trim an existing model to a box without re-meshing, run
  `-selectModel Src → -duplicateSelectedModel → -selectModel <copy> →
  -setReconstructionRegion box.rsbox → -selectTrianglesOutsideReconReg → -removeSelectedTriangles`.
  `-removeSelectedTriangles` creates a **new model** and selects it, so simplify, unwrap, texture
  and export then act on that one.
- A new High-detail reconstruction limited to a small region can come back **empty**. Trimming an
  existing Normal-detail model worked instead.
- A trimmed model can include the reconstruction's own textured closing surface at the bottom of
  the region. Raise the floor of the box until that surface is cut away.
- `.rsbox` files come in two layouts. One uses elements (`<widthHeightDepth>…</widthHeightDepth>`,
  `<CentreEuclid><centre>…`) and the other uses attributes (`widthHeightDepth="…"`,
  `<CentreEuclid centre="…"/>`). `xml_patch` with `elements=` only handles the element form.

**Texel size**

- The reconstruction **hull** (large, sparse closing triangles with tiny but non-zero UVs) distorts
  texel measurements. Measure over the surface only:
  `mm = 1000·sqrt(Σarea / (Σuv_area · tile_px²))`, after dropping triangles whose texel exceeds
  about 10× the nominal value.
- On a model trimmed from a larger reconstruction, that threshold isn't enough. The source region's
  flat lid gets real fixed-texel UVs and passes any texel threshold. Remove the hull by geometry
  instead: find flat planes of large faces, drop the faces on them, then flood through connected
  large faces to take the walls as well.

**Batches and jobs**

- A batch stops silently at the first failing command, and every command after it is skipped.
  Check `-getStatus` `lastError` and the output files, not only the exit code.
- An export once sat for about 80 minutes on a process id with no progress before it finished. Keep
  polling, and only kill a job if `runtime` stops advancing.
- Send big jobs (alignment, meshes of tens of millions of triangles, 8K UDIM texturing) through
  `rs_start_job` and poll every few minutes. Don't hold a tool call open.
- Report templates also work in delegate mode.

## Troubleshooting

| symptom | cause and fix |
|---|---|
| RealityScan isn't listed in the client after a restart | Usually invalid JSON, such as a missing comma or single backslashes. Validate the file, and make sure the app was fully quit from the tray. The Claude desktop app writes server logs to `%APPDATA%\Claude\logs\mcp-server-RealityScan.log`. |
| `check_connection` says RealityScan wasn't found | `REALITYSCAN_EXE` is wrong or points at another version's folder. |
| `cannot find a running RealityScan instance` (exit code 5) | No window with that instance name is running, or the window wasn't opened by `launch_gui`. |
| File tools say `outside the allowed roots` | The path is on the system drive or a network share, or `RS_MCP_ROOTS` doesn't include it. Add the folder to `RS_MCP_ROOTS` and restart the client. |
| Jobs fail at once with login or licence errors | RealityScan was never opened and logged in under this Windows user. |

## Tests

```powershell
.venv\Scripts\python tests\test_server.py
```

The tests run against `tests/stub_realityscan.py`, a stand-in that echoes its arguments and fakes a
few outputs, so they never launch RealityScan. They cover argv assembly, the whitelist, delegate
mode, background jobs, cancelling, file scoping, XML patching, compose and version reading. Each
test prints `ok` or `FAIL`, and the exit code is 1 if any test fails. When `REALITYSCAN_EXE` points
at a `.py` file, the runner starts it with the current Python.

The command whitelist comes from the "All commands" page of the RealityScan documentation. To
regenerate it, see `tools/scrape_commands.py`.

## Layout

```
src/realityscan_mcp/
  server.py     MCP tools
  runner.py     argv assembly, sync runs, background jobs, delegate courier
  commands.py   208-command whitelist (regenerate with tools/scrape_commands.py)
  files.py      root-scoped file and XML helpers
  config.py     executable discovery, environment, version reading
tests/          stub executable and tests
install.ps1     venv, install, config snippet
```

## Credits and license

MIT, see [LICENSE](LICENSE). This project builds on
[fkrn75/realityscan-mcp](https://github.com/fkrn75/realityscan-mcp) (MIT). It keeps that project's
command whitelist, the `-delegateTo` live-GUI mode and the `-silent`/`-stdConsole` handling, and
adds background jobs, root-scoped file and XML tools, and the export composer.
