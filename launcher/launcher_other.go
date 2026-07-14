//go:build !windows

package main

import (
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// resourceDir is the read-only folder holding the shipped inputs (uv,
// install.py, repo.json, icon.png). Inside a macOS .app the executable lives at
// Foo.app/Contents/MacOS/<exe>, and the inputs ship in Foo.app/Contents/Resources.
func resourceDir() string {
	exe, err := os.Executable()
	if err != nil {
		log.Fatal("Cannot determine executable path: ", err)
	}
	macosDir := filepath.Dir(exe)      // .../Contents/MacOS
	contents := filepath.Dir(macosDir) // .../Contents
	return filepath.Join(contents, "Resources")
}

// appName is derived from the .app bundle name (Foo.app -> Foo) so two apps
// built from this bootstrapper don't share a data directory.
func appName() string {
	exe, err := os.Executable()
	if err != nil {
		return "PygoBootstrap"
	}
	contents := filepath.Dir(filepath.Dir(exe)) // .../Foo.app/Contents
	appBundle := filepath.Dir(contents)         // .../Foo.app
	name := filepath.Base(appBundle)            // Foo.app
	name = strings.TrimSuffix(name, ".app")
	if name == "" || name == "." || name == string(filepath.Separator) {
		return "PygoBootstrap"
	}
	return name
}

// dataDir is the writable working directory. A .app bundle (e.g. in
// /Applications) is read-only, so generated files live under the user's
// Application Support directory instead.
func dataDir() string {
	home, err := os.UserHomeDir()
	if err != nil {
		log.Fatal("Cannot determine home directory: ", err)
	}
	return filepath.Join(home, "Library", "Application Support", appName())
}

func setupWorkingDir() {
	dir := dataDir()
	if err := os.MkdirAll(dir, 0755); err != nil {
		log.Fatal("Cannot create data directory: ", err)
	}
	if err := os.Chdir(dir); err != nil {
		log.Fatal("Cannot chdir to data directory: ", err)
	}
}

func uvBinaryName() string {
	return "uv"
}

// venvPython returns the path to a venv's Python interpreter, used to verify the
// venv is fully created (an interrupted `uv venv` can leave the folder present
// but empty). uv places the interpreter at bin/python off Windows.
func venvPython(venvFolder string) string {
	return filepath.Join(venvFolder, "bin", "python")
}

// hideWindow is a no-op off Windows (there is no console window to hide, and
// syscall.SysProcAttr has no HideWindow field on these platforms).
func hideWindow(cmd *exec.Cmd) {}

func amAdmin() bool {
	return os.Geteuid() == 0
}

// attemptElevation does nothing on macOS/Linux: there is no UAC-style
// re-launch. Returning false signals the caller to fall through to logging a
// real error rather than spawning a privilege prompt that cannot exist.
func attemptElevation(arg string) bool {
	return false
}
