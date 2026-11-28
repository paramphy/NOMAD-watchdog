# Running the NOMAD Experiment Watcher in the Background on Windows

This guide explains how to configure **Windows Task Scheduler** to start
the NOMAD Experiment Watcher automatically when you sign in to Windows,
run it in the background, and retry it if the process exits
unexpectedly.

The instructions reflect the setup previously discussed for this
project.

## 1. How the setup works

We use Windows Task Scheduler so the watcher does not need to be
launched manually from an open PowerShell window.

The task is configured to:

-   Start the watcher **at log on**.
-   Run under your normal Windows account.
-   Start the Python module from the project directory.
-   Retry the process after an unexpected exit.
-   Prevent multiple watcher instances from running simultaneously.

**Important:** The selected configuration is **Run only when user is
logged on**. The watcher starts after you sign in to Windows; it does
not start at the Windows sign-in screen before you log in.

## 2. Confirm the project directory and Python environment

The project directory used in the previous setup was:

``` text
C:\Users\parch602\Desktop\nomad-experiment-watcher
```

If you have moved the repository, use its current root directory
instead.

Open PowerShell and change to the project directory:

``` powershell
cd C:\Users\parch602\Desktop\nomad-experiment-watcher
```

Activate the same Python virtual environment you use to run the watcher
manually, if applicable. For example, if it is named `.venv`:

``` powershell
.\.venv\Scripts\Activate.ps1
```

Find the full path of the Python executable:

``` powershell
python -c "import sys; print(sys.executable)"
```

Copy the path printed by this command. Task Scheduler must use this
exact executable so that it uses the correct Python environment and
installed dependencies.

Before scheduling the task, verify that the watcher starts manually from
the project root:

``` powershell
python -m nomad_experiment_watcher.main
```

Stop the manual run with `Ctrl+C` before testing the scheduled task. Do
not run the scheduled instance and a manual instance simultaneously.

## 3. Create the scheduled task

1.  Open the Windows **Start** menu.
2.  Search for and open **Task Scheduler**.
3.  In the Actions panel, select **Create Task...** (not *Create Basic
    Task*).

### General tab

Set the following:

-   **Name:** `NOMAD Experiment Watcher`
-   **Description:**
    `Runs the NOMAD Experiment Watcher in the background after Windows logon.`
-   Select **Run only when user is logged on**.
-   Leave **Run with highest privileges** unchecked unless the
    application genuinely requires elevated permissions.

Run the task under the same Windows account that has access to the
repository, `.env`, and experimental-data directories.

### Triggers tab

1.  Select **New...**
2.  Set **Begin the task** to **At log on**.
3.  Select **Specific user** and choose your normal Windows account, if
    shown.
4.  Ensure **Enabled** is checked.
5.  Select **OK**.

This trigger starts the watcher whenever that Windows user signs in,
including after a system restart.

### Actions tab

1.  Select **New...**
2.  Set **Action** to **Start a program**.
3.  Enter the following values.

**Program/script**

Paste the full Python executable path copied in Section 2. For example:

``` text
C:\Users\parch602\Desktop\nomad-experiment-watcher\.venv\Scripts\python.exe
```

Use the actual path from your computer; the example may not match your
environment.

**Add arguments (optional)**

``` text
-m nomad_experiment_watcher.main
```

**Start in (optional)**

``` text
C:\Users\parch602\Desktop\nomad-experiment-watcher
```

Use the project root here, not the `src` directory or package directory.
This matters because the application uses project-relative paths for
configuration, `.env`, logs, the database, and experiment folders.

Select **OK**.

### Conditions tab

Review the conditions and disable any that would prevent the watcher
from running when you need it.

For example, clear **Start the task only if the computer is on AC
power** if you also need the watcher to run on battery. Follow your
workstation's power-management requirements.

### Settings tab

Configure:

-   Enable **Allow task to be run on demand**.
-   Enable **If the task fails, restart every:** `1 minute`.
-   Set **Attempt to restart up to:** `3 times`.
-   For **If the task is already running, then the following rule
    applies**, select **Do not start a new instance**.

These settings let Task Scheduler retry the watcher after an unexpected
process exit and prevent overlapping instances.

Select **OK** to save the task.

## 4. Check the `.env` file and credentials

The watcher uses its NOMAD Personal Access Token (PAT) for
authentication.

The project-root `.env` file should contain:

``` dotenv
NOMAD_PAT=your_actual_nomad_personal_access_token
```

Keep the real token private. Do not put it in the task's arguments,
source code, screenshots, or logs.

Check that:

-   The file is named exactly `.env`, not `.env.txt`.
-   It is located in the project root.
-   The Windows account running the task can read it.
-   `.env` is excluded from Git and is never committed to the
    repository.

The task should run under the same Windows account used for normal
manual execution so that it can access the credentials and
experimental-data folders.

## 5. Start and test the task

In Task Scheduler:

1.  Find **NOMAD Experiment Watcher** in the Task Scheduler Library.
2.  Right-click it and select **Run**.
3.  Check **Last Run Result** and inspect the watcher log.
4.  Confirm that the watcher initializes successfully, connects to
    NOMAD, and starts monitoring the configured experiment directories.

The log file used in the previous setup was:

``` text
logs\watcher.log
```

From the project root, inspect its latest lines in PowerShell:

``` powershell
Get-Content .\logs\watcher.log -Tail 50
```

If the log path or filename has changed in your current configuration,
use the path configured by the application.

To stop the scheduled instance, right-click the task and select **End**.
Do not launch a second manual copy while the scheduled task is running.

## 6. Test automatic startup after a Windows restart

After confirming that the task runs successfully on demand:

1.  Restart Windows.
2.  Sign in using the Windows account configured in the task.
3.  Open Task Scheduler.
4.  Check that **NOMAD Experiment Watcher** has run.
5.  Inspect `logs\watcher.log` and confirm that a new startup sequence
    appears.

Because the trigger is **At log on**, the watcher starts after you sign
in.

## 7. Troubleshooting

### The task starts and immediately stops

-   Check `logs\watcher.log` for a Python traceback.
-   Confirm that **Program/script** points to the correct Python
    executable.
-   Confirm that **Add arguments** contains
    `-m nomad_experiment_watcher.main`.
-   Confirm that **Start in** is set to the project root.
-   Run the same command manually from that directory to identify
    application errors.

### The task cannot find the Python module

The task may be using a different Python installation or virtual
environment from the one used manually.

Run this command in the environment that works:

``` powershell
python -c "import sys; print(sys.executable)"
```

Update **Program/script** with that exact executable path.

### NOMAD authentication fails

Check that `.env` is present in the project root, contains the
`NOMAD_PAT` variable, and is readable by the account running the task.
Never paste the PAT into diagnostic logs or share it when asking for
help.

### The task does not start after restarting Windows

-   Confirm that the task is **Enabled**.
-   Confirm that the trigger is **At log on** for the correct Windows
    account.
-   Sign in to that account; this configuration does not run before
    sign-in.
-   Check the task's **History** tab and **Last Run Result** for errors.

### The watcher is already running

The task is configured not to start another instance. Check Task Manager
and Task Scheduler before launching a manual copy. End the existing task
cleanly before testing another instance.

## 8. Operational notes

-   Keep the computer powered on and connected to the network while
    experiments need to be monitored.
-   Windows sleep or hibernation can interrupt monitoring. Configure
    power and sleep settings according to the workstation's operational
    requirements.
-   Task Scheduler can restart the process after an unexpected exit, but
    it cannot guarantee that every application-level error is
    recoverable. Inspect the watcher logs after a failure.
-   The watcher's own upload retry logic handles eligible NOMAD upload
    retries. Task Scheduler's restart policy is a separate,
    process-level safeguard.
-   Do not delete or recreate `database\watcher.db`, archive-cache
    files, or experimental data as part of routine task setup or
    troubleshooting.
-   Keep `.env` private and excluded from version control.

## Configuration summary

  Setting              Value
  -------------------- --------------------------------------------
  Task name            `NOMAD Experiment Watcher`
  Trigger              At log on
  Run mode             Run only when user is logged on
  Program              Full path to the working Python executable
  Arguments            `-m nomad_experiment_watcher.main`
  Start in             Project root
  Restart on failure   Every 1 minute
  Restart attempts     3
  Multiple instances   Do not start a new instance
  Log file             `logs\watcher.log`
  Credential source    `NOMAD_PAT` in project-root `.env`

------------------------------------------------------------------------

**Suggested repository location:** `docs/windows-background-service.md`
