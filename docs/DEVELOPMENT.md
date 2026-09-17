# Development and validation

[Documentation index](README.md) | [Project overview](../README.md)

The [validation workflow](../.github/workflows/validation.yml) runs on pushes, pull requests and manual dispatch. It uses Windows with Python 3.10, 3.12 and 3.14, a read-only repository token, mocked tenant calls and invented evidence. No customer credentials or tenant access are required.

## Run the checks locally

From the repository root, use the Python environment prepared in [the environment guide](PYTHON_ENVIRONMENT.md):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip check
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\Test-PowerShellSyntax.ps1
.\.venv\Scripts\python.exe tools/check_docs.py
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe tools/validate_offline_report.py
```

PowerShell validation parses all scripts in the root, `tools/` and `tests/`; it does not execute setup or cleanup. The documentation check validates local Markdown file links and heading anchors, including archived guides. It does not contact external websites. Review Microsoft references separately when changing API or permission behavior.

The synthetic report check creates an isolated temporary package, runs the offline CLI, and audits the resulting HTML and workbook for evidence integrity, cross-output consistency and Excel compatibility. Its fixtures contain invented tenant data. Generated smoke-test artifacts are temporary and are not uploaded by CI. The regular regression suite also verifies that restricted and offline paths cannot invoke excluded collectors.

## Dependency updates

[requirements.txt](../requirements.txt) declares supported direct dependency ranges. [requirements.lock.txt](../requirements.lock.txt) fixes the tested direct and transitive versions and includes those ranges to catch incompatible changes. Use the lock file for operator installations and CI. It pins versions; it does not verify downloaded package hashes. See [pip's repeatable-install guidance](https://pip.pypa.io/en/stable/topics/repeatable-installs/).

For an intentional update, create a fresh environment outside the customer workspace, install from `requirements.txt`, and inspect `python -m pip freeze`. Update the lock with only application dependencies, retaining platform/Python markers such as the Python 3.10 `exceptiongroup` dependency. Do not copy unrelated packages from a long-lived development environment. Run the commands above and review the CI matrix before adopting the update. Do not regenerate the lock automatically during an assessment.

## Reliability regression coverage

- Environment configuration tests reject missing selected files and malformed values before authentication, preserve literal quoted credentials, and verify safe configuration summaries.
- Setup tests mock credential validation, identity matching, expiration, interrupted consent and atomic persistence. They never create a customer credential.
- HTTP tests mock requests and sleeps, exercising throttling, retry limits and preservation of partial pages.
- Checkpoint tests interrupt synthetic pipelines and verify that saved completed evidence can be replayed with explicit gaps for unfinished services.

The live collectors use bounded retries on transient read failures. Microsoft Graph's [throttling guidance](https://learn.microsoft.com/en-us/graph/throttling) requires honoring `Retry-After`. A server delay that exceeds the remaining retry budget stops that read; it is not shortened to send an early retry. References verified September 17, 2026.
