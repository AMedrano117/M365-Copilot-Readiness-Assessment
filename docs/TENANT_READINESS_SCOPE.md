# Tenant readiness scope

New assessments evaluate the Microsoft 365 tenant technical baseline. Pilot
membership, business use cases, success measures, pilot outcomes and rollout
approval belong to a separate engagement. They are not required inputs, control
checks, completion criteria or ways to close tenant security findings.

The recorded outcomes are `Tenant remediation required`, `Tenant readiness
unconfirmed`, `Tenant readiness with conditions` and `Tenant baseline ready`.
Readiness depends on the existing required technical controls and their dated,
scoped evidence. Missing evidence remains unknown. A ready baseline is not
authorization to deploy Copilot or expand a rollout.

Current explicit device-setting failures retain their priority and can block
tenant readiness without a pilot roster. An optional roster does not exclude
devices from tenant findings. Inactivity, ambiguous identity, reporting errors
and unverified protection-provider observations retain their qualifications.
Unknown pilot membership no longer generates a device-posture gap.

Tenant-wide operator attestations remain supported. Only tenant-scope reviews
can establish tenant controls; pilot/expansion reviews and planning material are
retained as optional context. Incomplete optional planning does not discard valid
tenant reviews, and business approval cannot close a failed technical control.
Provider security reviews remain visible; business use cases are outside scope.
Aggregate usage can provide context, but does not establish control effectiveness.

The active tenant checklist excludes `SCOPE.PILOT` and `EXTERNAL_AI.OUTCOMES`;
their exclusion is recorded explicitly. Technical service ownership, applicable
security/legal obligations, real risk observations and remediation remain in scope.

The shared result records `assessment_scope: tenant_readiness` and
`assessment_scope_version: 1.0`. HTML, the readiness summary, workbooks and
machine-readable output use this scope. Legacy field/tab names can remain for
reader compatibility; current progress contains technical review stages only.
Original native records remain available in the technical workbook and saved
collection. Supplied planning material is retained for traceability.

Frozen historical snapshots keep their recorded scope and conclusions when
rendered. To apply the new scope, rebuild a separate package from a saved
collection using `--mode offline --collection-input PATH --run-type Standalone`.
Completed snapshots and assessment history are not rewritten.

A comparison across legacy rollout scope and tenant-readiness scope is recorded
as `NotComparable` with `assessment_scope_changed`. The scope change cannot be
credited as technical improvement or regression. Comparisons between runs using
the same tenant-readiness scope still require the existing ownership, version,
collection coverage and item evidence checks.
