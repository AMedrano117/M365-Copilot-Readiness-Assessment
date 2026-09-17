# Python environment and OneDrive

[Documentation index](README.md) | [Project overview](../README.md)

Keep the Python virtual environment outside OneDrive when the repository is in a
synced folder. An environment contains thousands of replaceable dependency files;
it can be recreated from `requirements.lock.txt` on each machine. `.gitignore` excludes
it from Git, but does not exclude it from OneDrive.

OneDrive's **path too long** message refers to the combined folder and file names,
not the size of a file or the environment. See [Microsoft's path limits](https://support.microsoft.com/en-US/onedrive/what-are-file-path-length-limits).

## Recommended setup for a repository in OneDrive

Run these commands from the repository folder. They create a new environment under
your local application-data folder, outside OneDrive:

```powershell
$AssessmentEnv = Join-Path $env:LOCALAPPDATA 'venvs\m365-copilot-assessment'
py -3 -m venv $AssessmentEnv
$AssessmentPython = Join-Path $AssessmentEnv 'Scripts\python.exe'
& $AssessmentPython -m pip install -r .\requirements.lock.txt
& $AssessmentPython -m pip check
& $AssessmentPython .\main.py --help
```

Use Python 3.10 or later. Installing dependencies needs network access; saved
assessments can then be rebuilt offline. Recreate an environment in its new
location instead of moving the existing folder, because installed scripts can
contain absolute paths. [Python's environment guidance](https://docs.python.org/3/library/venv.html).

The lock file pins the tested direct and transitive dependencies. `requirements.txt`
retains the supported version ranges for intentional development updates. See
[development and validation](DEVELOPMENT.md) for the automated checks and update procedure.

The assessment arguments stay the same. In runbook examples, replace
`.\.venv\Scripts\python.exe` with `& $AssessmentPython`:

```powershell
& $AssessmentPython .\main.py --mode live --check-connections
& $AssessmentPython .\main.py --mode live
& $AssessmentPython .\main.py --mode offline `
  --collection-input '<saved collection.json>' `
  --open-html-report
```

In a new PowerShell terminal, set the executable path again:

```powershell
$AssessmentPython = Join-Path $env:LOCALAPPDATA 'venvs\m365-copilot-assessment\Scripts\python.exe'
```

If replacing an existing `.venv`, verify the new environment before removing the
old dependency folder. The repository, `.env`, collections, reports and tenant
exports stay in their current locations. Changing the environment does not move them.

## Existing environments with the old Graph SDK

The assessment uses `azure-identity` and `httpx` for Graph requests. It does not
use `msgraph-sdk`, `msgraph-core`, or the generated Kiota SDK packages. Older
environments can still contain them: `pip install -r requirements.lock.txt` installs
required dependencies but does not uninstall packages removed from that file.

The generated `msgraph` tree has particularly long paths and many small files.
Removing that obsolete SDK from this project's environment resolves those paths
while preserving the existing `.\.venv\Scripts\python.exe` commands. A fresh
environment installed from the current `requirements.lock.txt` does not include it.

Recreating the environment outside OneDrive also prevents future dependencies
and bytecode caches from becoming sync work.
