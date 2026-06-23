package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

var pythonBinaryPath string

type Config struct {
	UsePythonW    bool   `json:"use_pythonw"`
	VenvFolder    string `json:"venv_folder"`
	PythonVersion string `json:"python_version"`
}

var config Config

func findPython(path string, info os.FileInfo, err error) error {
	checkError("walkPath error", err)

	if info.IsDir() {
		return nil
	}

	base := filepath.Base(path)

	if runtime.GOOS == "windows" {
		if config.UsePythonW && strings.EqualFold(base, "pythonw.exe") {
			pythonBinaryPath = path
			return filepath.SkipDir
		} else if !config.UsePythonW && strings.EqualFold(base, "python.exe") {
			pythonBinaryPath = path
			return filepath.SkipDir
		}
	} else if base == "python" {
		pythonBinaryPath = path
		return filepath.SkipDir
	}

	return nil
}

func checkError(message string, err error) {
	if err != nil {
		print(message)
		if !amAdmin() {
			print("Restarting as admin...")
			arg := ""
			if strings.Contains(message, "venv") {
				print("And deleting venv!")
				arg = "-delete-venv"
			} else {
				print("Without deleting venv.")
			}
			// On platforms with no UAC (macOS/Linux) attemptElevation returns
			// false, so we fall through to logging + log.Fatal below instead of
			// silently exiting.
			if attemptElevation(arg) {
				os.Exit(0)
			}
		}

		logMsg := fmt.Sprintf("%s: %v", message, err)

		logDir := "logs"
		if _, err := os.Stat(logDir); os.IsNotExist(err) {
			err := os.MkdirAll(logDir, 0755)
			if err != nil {
				log.Fatal("Cannot create log directory: ", err)
			}
		}

		logFile := filepath.Join(logDir, "launcher-error.log")

		f, err := os.OpenFile(logFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
		if err != nil {
			log.Fatal("Cannot open log file: ", err)
		}
		defer func(f *os.File) {
			err := f.Close()
			if err != nil {
				log.Print("Failed to close file: ", err)
			}
		}(f)

		currentTime := time.Now().Format("2006-01-02 15:04:05")
		timestampedLogMsg := fmt.Sprintf("%s %s\n", currentTime, logMsg)

		if _, err := f.WriteString(timestampedLogMsg); err != nil {
			log.Println("Cannot write to log file: ", err)
		}

		log.Fatal(logMsg)
	}
}

func uvPath() string {
	return filepath.Join(resourceDir(), uvBinaryName())
}

func uvEnv() []string {
	pythonInstallDir := filepath.Join(dataDir(), "python")
	return append(os.Environ(), "UV_PYTHON_INSTALL_DIR="+pythonInstallDir)
}

func runUV(f *os.File, args ...string) error {
	cmd := exec.Command(uvPath(), args...)
	cmd.Env = uvEnv()
	cmd.Stderr = f
	hideWindow(cmd)
	return cmd.Run()
}

func main() {
	// Establish the working directory before touching any relative path. On
	// Windows this chdirs next to the exe; on macOS it creates and chdirs into
	// ~/Library/Application Support/<AppName>/.
	setupWorkingDir()

	fmt.Println("Started!")

	if _, err := os.Stat("./logs"); os.IsNotExist(err) {
		err = os.Mkdir("./logs", 0755)
		if err != nil {
			log.Fatal(err)
		}
	}

	f, err := os.OpenFile("logs/python_crash.log", os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0644)
	if err != nil {
		log.Fatal(err)
	}
	defer f.Close()

	_, err = f.WriteString("Logging stderr for run at datetime: " + time.Now().Format("2006-01-02 15:04:05") + "\n")
	if err != nil {
		log.Fatal(err)
	}

	data, err := os.ReadFile(filepath.Join(resourceDir(), "repo.json"))
	checkError("Error reading repo.json", err)

	err = json.Unmarshal(data, &config)
	checkError("Error parsing repo.json", err)

	for _, arg := range os.Args {
		if arg == "-delete-venv" {
			print("Deleting venv...")
			err := os.RemoveAll(config.VenvFolder)
			checkError("Failed to delete venv folder", err)
		}
	}

	pythonVersion := config.PythonVersion
	if pythonVersion == "" {
		pythonVersion = "3.11"
	}

	freshVenv := false
	_, err = os.Stat(config.VenvFolder)
	if os.IsNotExist(err) {
		freshVenv = true
		fmt.Println("Creating venv with uv (python " + pythonVersion + ")...")
		err = runUV(f, "venv", config.VenvFolder, "--python", pythonVersion)
		checkError("Failed to create venv with uv, see python_crash.log", err)
	} else if err != nil {
		checkError("Error checking venv directory", err)
	} else {
		fmt.Println("Venv directory already exists, skipping venv creation")
	}

	err = filepath.Walk(config.VenvFolder, findPython)
	checkError("Error walking the path", err)

	// Show a tkinter "please wait" dialog on first setup while installing base requirements
	var waitDialog *exec.Cmd
	if freshVenv {
		absVenvPython, _ := filepath.Abs(pythonBinaryPath)
		tkScript := `import tkinter as tk; root = tk.Tk(); root.title("Install"); root.attributes("-topmost", True); tk.Message(root, text="Installing prerequisites.\nPlease wait...", padx=20, pady=20).pack(); root.mainloop()`
		waitDialog = exec.Command(absVenvPython, "-c", tkScript)
		hideWindow(waitDialog)
		_ = waitDialog.Start()
	}

	fmt.Println("Installing base requirements...")
	baseReqArgs := []string{
		"pip", "install", "--python", config.VenvFolder,
		"requests", "googletrans~=4.0.0rc1", "PyQt6", "PyQt6-Qt6", "dulwich~=0.21.5",
	}
	err = runUV(f, baseReqArgs...)

	if waitDialog != nil && waitDialog.Process != nil {
		_ = waitDialog.Process.Kill()
		_ = waitDialog.Wait()
	}

	checkError("Failed to install base requirements with uv, see python_crash.log", err)

	absNewVenvPythonBinaryPath, err := filepath.Abs(pythonBinaryPath)
	checkError("Cannot resolve absolute path for python binary", err)

	absPythonScriptPath := filepath.Join(resourceDir(), "install.py")
	fmt.Println("Python Script Path: ", absPythonScriptPath)
	fmt.Println("Venv Python Binary Path: ", absNewVenvPythonBinaryPath)

	absUvPath, _ := filepath.Abs(uvPath())
	absVenvPath, _ := filepath.Abs(config.VenvFolder)

	installEnv := append(uvEnv(),
		"UV_PATH="+absUvPath,
		"VENV_PATH="+absVenvPath,
		"RESOURCE_DIR="+resourceDir(),
	)

	cmd := exec.Command(absNewVenvPythonBinaryPath, absPythonScriptPath)
	cmd.Env = installEnv
	cmd.Stderr = f
	hideWindow(cmd)

	for counter := 0; counter <= 3; counter++ {
		err = cmd.Run()
		if err == nil {
			break
		}

		exitError, ok := err.(*exec.ExitError)
		if !ok || exitError.ExitCode() != 99 || counter >= 3 {
			deleteVenv := false
			for _, arg := range os.Args {
				if arg == "-delete-venv" {
					deleteVenv = true
					break
				}
			}

			if !amAdmin() {
				// Not elevated. On Windows, relaunch elevated to retry.
				if attemptElevation("") {
					os.Exit(0)
				}
				// non-Windows (no UAC): fall through to local recovery below.
			} else {
				// Already privileged.
				if deleteVenv {
					checkError("Install failed even after resetting venv. Giving up.", err)
				}
				// On Windows, relaunch elevated with a fresh venv.
				if attemptElevation("-delete-venv") {
					os.Exit(0)
				}
				// non-Windows: fall through to local recovery below.
			}

			// Reached only when elevation/relaunch is unavailable (macOS/Linux):
			// reset the venv ourselves so the next launch starts clean, then
			// surface a logged fatal error instead of looping.
			print("Install failed, resetting venv...")
			_ = os.RemoveAll(config.VenvFolder)
			checkError("Install failed and could not recover, see python_crash.log", err)
			os.Exit(1)
		}

		// Exit code 99 means install.py wants a restart — retry
		cmd = exec.Command(absNewVenvPythonBinaryPath, absPythonScriptPath)
		cmd.Env = installEnv
		cmd.Stderr = f
		hideWindow(cmd)
	}

	checkError("install.py script error, see python_crash.log", err)
}
