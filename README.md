# NOMAD Experiment Watcher

A Python-based tool for monitoring experimental-data folders, organizing measurement files into archives, and uploading experiment data to a NOMAD Oasis. It also supports automatic Coauthor-group access for configured NOMAD uploads.

## Features

- Detects and registers experiment folders.
- Maintains experiment and upload state in a local SQLite database.
- Creates measurement archives for supported data types.
- Uploads archives to NOMAD and verifies upload status.
- Applies configured NOMAD Coauthor-group access.
- Retries eligible upload operations and records activity in logs.

## Project structure

```text
nomad-experiment-watcher/
├── config/
│   └── config.yaml             # Watcher, experiment and NOMAD settings
├── database/
│   └── watcher.db              # Local SQLite state database (created at runtime)
├── docs/
│   └── windows-background-service.md
├── Experimental_Data/          # Experiment folders monitored by the watcher
├── logs/                       # Runtime logs
├── src/
│   └── nomad_experiment_watcher/
│       ├── main.py             # Application entry point
│       ├── config.py           # Configuration loading
│       ├── database.py         # Database operations
│       ├── experiments/
│       │   ├── manager.py      # Experiment discovery and registration
│       │   ├── metadata.py     # Experiment metadata handling
│       │   └── archive_manager.py
│       ├── monitoring/
│       │   └── folder_monitor.py
│       └── nomad/
│           ├── client.py       # NOMAD API client
│           └── uploader.py     # Upload and group-sharing operations
├── .env                        # Local secrets; do not commit
└── pyproject.toml              # Python project/dependency configuration
```

Some files and directories (for example, the database, logs, and runtime archives) are created when the application runs. The exact structure may vary slightly with local configuration.

## Setup

1. Clone the repository and open a terminal in the project root.
2. Create and activate a Python virtual environment.
3. Install the project and its dependencies using the repository's configured installation method. For an editable install, use:

   ```powershell
   pip install -e .
   ```

4. Create a `.env` file in the project root and add your NOMAD Personal Access Token:

   ```dotenv
   NOMAD_PAT=your_nomad_personal_access_token
   ```

5. Review `config/config.yaml`. Set the NOMAD Oasis URL, experiment paths, monitoring options, and sharing settings for your environment.

Keep `.env` private. Never commit your PAT or other credentials to GitHub.

## Run

From the repository root, with the virtual environment activated:

```powershell
python -m nomad_experiment_watcher.main
```

The watcher reads its settings from `config/config.yaml`, uses the project-root `.env` for credentials, and monitors the configured experiment directories.

## Run automatically on Windows

To run the watcher in the background and start it again after a Windows restart, configure it with **Windows Task Scheduler**.

See [`docs/windows-background-service.md`](docs/windows-background-service.md) for the setup instructions, restart policy, and troubleshooting.

## Important data and security notes

- Back up `database/watcher.db`; it stores local watcher state and upload tracking.
- Do not delete the database or archive cache as a routine troubleshooting step.
- Keep `.env` out of version control.
- Keep the computer awake and connected to the network while experiments are being monitored.
- NOMAD publishing remains a separate, manual step.
