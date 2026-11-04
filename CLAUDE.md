# Repository working guidance

## Inspection and permission efficiency

- Prefer built-in Read, Grep, and Glob tools for routine repository inspection, file discovery, and text searches. Do not create temporary Python or PowerShell scripts just to read files or search text.
- Prefer existing, reusable repository diagnostic and test utilities. When a new diagnostic is necessary, keep it focused and reusable rather than creating a one-off implementation of business logic.
- Carry out routine local inspection, testing, deterministic synthetic fixture creation, report rendering, and implementation work within the requested scope without repeatedly asking for permission.
- Preserve existing Claude configuration and unrelated user changes. Tool allowlists are execution capabilities, not user approval for actions that require explicit approval below. Follow these approval requirements even when an existing allowlist permits the command.
- Keep work bounded to the requested change and avoid unrelated refactoring.

## Shell command efficiency

Prefer separate, simple read-only shell commands over large compound commands when practical.
Avoid combining multiple inspection operations with &&, ;, pipelines, or temporary shell variables when separate operations would reduce permission prompts.
Prefer built-in Read, Grep, and Glob for repository content inspection.
Use diff and checksum utilities when useful for deterministic before/after validation.
Keep temporary analysis within the repository or Claude's own temporary workspace.

## Actions requiring explicit approval

Obtain explicit user approval before:

- Destructive Git operations, including discarding work, hard resets, cleaning untracked files, deleting branches, or rewriting history.
- Any `git push` other than the pull-request workflow below, including any push to `main` and any force push.
- Installing packages, dependencies, tools, or software.
- Accessing, using, creating, changing, or disclosing credentials, secrets, tokens, or authentication configuration.
- Making changes outside this repository.
- Modifying any live Microsoft 365 service, tenant, configuration, permissions, or data.

Pull-request workflow (no separate approval needed): when a task asks for a pull request, push the task's feature branch (never `main`) without force, open the pull request, and push later non-force commits to that branch to update it. Do not merge unless explicitly asked.

Do not infer approval for these actions from a general implementation request or a tool permission. When approval is needed, describe the concrete action and its scope. Complete independent authorized local preparation first. These instructions do not override sandbox or platform permission controls.

## Layered, test-first validation

1. Define intended behavior, expected results, and acceptance criteria before changing production code.
2. Start with the fastest appropriate validation: unit tests for isolated functions, invariant tests for rules that must always hold, and deterministic synthetic scenarios for decision or business logic.
3. For a defect, reproduce it with the smallest fixture possible, add a failing regression test, and confirm the expected failure. Make the smallest production fix, confirm the test passes, and retain the regression permanently.
4. Declare synthetic scenario expectations independently before production logic runs. Never derive expectations from actual output merely to make tests pass.
5. Tests, diagnostics, and production execution must reuse the same production logic. Do not create simplified test-only implementations of matching, classification, correlation, or other business rules.
6. Provide focused diagnostics for individual objects or decisions where useful, so a single issue can be investigated without rerunning the entire dataset.
7. Use a small deterministic reference dataset for end-to-end validation before running large production datasets.
8. Separate presentation from business logic. For HTML, CSS, branding, wording, or layout changes, use existing normalized results or a small report fixture and regenerate only the presentation. Do not rerun expensive collection or comparison logic just to test rendering.
9. Preserve complete evidence. Display limits and report simplification must not change source data, findings, counts, classifications, or actionability.
10. Run large production or customer datasets only after faster tests pass, as final integration, scale, or release validation. Obtain any required approval for credentials or live-service modifications first.

If expected behavior and production behavior disagree, determine whether the implementation, fixture, test, expectation, or design is wrong. Do not silently change expectations or production logic just to get a green test.

At completion, summarize the behaviors validated, validation layers run, defects or regressions discovered, production changes made, remaining gaps, and whether the requested acceptance criteria passed. Do not report only a test count.
