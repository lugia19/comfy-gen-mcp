//go:build windows

package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"

	"golang.org/x/sys/windows"
)

// resourceDir is the read-only folder holding the shipped inputs (uv.exe,
// install.py, repo.json, icon.png). On Windows it lives beside the exe.
func resourceDir() string {
	exe, err := os.Executable()
	if err != nil {
		return "installer-resources"
	}
	return filepath.Join(filepath.Dir(exe), "installer-resources")
}

// dataDir is the writable working directory (python runtime, venv, logs, repo
// clone, the "installing" marker). On Windows this is the exe's folder, keeping
// the portable "everything next to the exe" layout.
func dataDir() string {
	exe, err := os.Executable()
	if err != nil {
		return "."
	}
	return filepath.Dir(exe)
}

func setupWorkingDir() {
	if err := os.Chdir(dataDir()); err != nil {
		fmt.Println("Warning: could not chdir to data dir:", err)
	}
}

func uvBinaryName() string {
	return "uv.exe"
}

func hideWindow(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
}

func amAdmin() bool {
	elevated := windows.GetCurrentProcessToken().IsElevated()
	fmt.Printf("admin %v\n", elevated)
	return elevated
}

// attemptElevation re-launches this executable elevated via ShellExecute
// "runas" (the UAC prompt), optionally appending arg. It returns true if the
// elevated process was launched (the caller should then exit), false on error.
func attemptElevation(arg string) bool {
	verb := "runas"
	exe, _ := os.Executable()
	cwd, _ := os.Getwd()

	allArgs := append([]string{}, os.Args[1:]...)
	if arg != "" {
		allArgs = append(allArgs, arg)
	}
	args := strings.Join(allArgs, " ")

	verbPtr, _ := syscall.UTF16PtrFromString(verb)
	exePtr, _ := syscall.UTF16PtrFromString(exe)
	cwdPtr, _ := syscall.UTF16PtrFromString(cwd)
	argPtr, _ := syscall.UTF16PtrFromString(args)

	var showCmd int32 = 1
	if err := windows.ShellExecute(0, verbPtr, exePtr, argPtr, cwdPtr, showCmd); err != nil {
		fmt.Println(err)
		return false
	}
	return true
}
