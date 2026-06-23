# CLAUDE.md

Guidance for working in this repo.

## What this is

A reusable **Go + Python bootstrapper/updater**. The Go launcher uses [`uv`](https://github.com/astral-sh/uv)
to download a Python runtime and create a venv, installs base GUI packages, then runs `install.py`,
which clones/pulls a configured GitHub repo, installs that repo's requirements, and launches its
startup script. It ships as a native launcher per platform: a `.exe` on Windows and a signed
**universal `.app`** on macOS. Builds run **on Windows** and cross-compile every target.

This repo is a *template*: `repo.json` and `resources/icon.png` are per-deployment inputs (gitignored),
not committed. `repo-example.json` is the committed template.

## Layout

- `launcher.go` — shared launcher logic (platform-agnostic). No `windows`/`syscall` imports.
- `launcher_windows.go` (`//go:build windows`) — the only file importing `golang.org/x/sys/windows`
  / using `syscall`. UAC elevation (`ShellExecute "runas"`), `IsElevated`, `HideWindow`.
- `launcher_other.go` (`//go:build !windows`) — macOS/Linux: `geteuid` admin check, no-op elevation,
  bundle-relative resource dir, `~/Library/Application Support` data dir.
- `install.py` — clone/pull (dulwich) + install requirements (uv) + PyQt6 progress UI + launch app.
- `tools/mkicon/` — **separate Go module**; pure-Go `icon.png` → `app.ico` + `app.icns` converter.
- `build-all.ps1` — the build entrypoint (preflight, tool fetch/cache, build, sign, zip).

## Cross-platform rules (important)

- **Never reference Windows-only symbols in `launcher.go`.** `golang.org/x/sys/windows`,
  `windows.ShellExecute`, `IsElevated`, and `syscall.SysProcAttr{HideWindow}` fail to **compile** on
  darwin — `runtime.GOOS` guards do not help. Anything platform-specific goes behind a helper defined
  in *both* `launcher_windows.go` and `launcher_other.go`: `resourceDir`, `dataDir`,
  `setupWorkingDir`, `uvBinaryName`, `hideWindow`, `amAdmin`, `attemptElevation`.
- `attemptElevation` returns **false** off Windows (no UAC); callers must fall through to a logged
  error, never loop trying to elevate.
- Keep Windows runtime behavior identical to before when refactoring.

## Resource dir vs data dir

The launcher reads inputs from `resourceDir()` and writes generated files to `dataDir()`, passing
`RESOURCE_DIR` to `install.py` (which reads `repo.json` + icon from there, writes logs/repo/marker to
CWD = data dir).

| | Resource dir (read-only inputs) | Data dir (writable: python, venv, logs, repo) |
|---|---|---|
| Windows | `<exeDir>\installer-resources` | `<exeDir>` (portable, next to the exe) |
| macOS | `Foo.app/Contents/Resources` | `~/Library/Application Support/<AppName>/` |

`<AppName>` on macOS is derived from the `.app` bundle name and must match `$APP_NAME` in
`build-all.ps1` (it's also the macOS data-dir name, so keep it stable across releases).

## Build / verify

- Build everything: `.\build-all.ps1` (flags: `-WindowsOnly`, `-MacOnly`). First run downloads tools
  into the gitignored `resources\` cache and reuses them after. Outputs land in `builds\`.
  Requires Go, and WSL+`zip` for the macOS bundle.
- Compile check all targets (do this after launcher changes):
  ```
  foreach ($t in @(@('windows','amd64'),@('darwin','amd64'),@('darwin','arm64'))) {
    $env:GOOS=$t[0]; $env:GOARCH=$t[1]; go build -o $null .; go vet .
  }
  ```
- The macOS `.app` is **ad-hoc** signed via `rcodesign` (works from Windows; no Apple account).
  Gatekeeper still warns on first launch — users right-click → Open or `xattr -dr com.apple.quarantine`.

## Gotchas

- `repo.json`'s `icon` should be a **`.png`** (Qt loads it cross-platform; the build converts it to
  `.ico`/`.icns` for the OS-level app icons).
- `install.py`'s Windows-only bits (`CREATE_NO_WINDOW`, `ctypes.windll`, `SetCurrentProcessExplicit
  AppUserModelID`) are already guarded by `os.name == 'nt'`.
- A bare `go build` emits a `pygo-bootstrap` binary (gitignored); prefer `go build -o $null .` for
  checks.
