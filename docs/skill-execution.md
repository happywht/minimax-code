# Durable Native skills

`skill.invoke` and Code Review use the existing Native RunService admission,
AgentCore, tool gates, run journal and budget ledger. They require an explicit
Native session and stable request key. They do not call a second SkillRuntime
conversation loop. Teams and direct ungoverned paid roots remain fail-closed;
their unification is separate work.

The server copies a registry-resolved skill ID/version, instructions, selected
provider tool schemas/identity, iteration limit, result format, model/provider,
workspace identity, context and permission rules into `metadata.skill_execution`.
A digest guards the saved profile. Public callers supply intent only. The
intent fingerprint excludes newly resolved defaults, so a lost acknowledgement
can replay the first accepted run even after settings change. Queued runs keep
their original workspace/model/instructions/context. Before execution, recovery
and continuation, the selected skill and tool providers must still be enabled
and compatible; incompatible dependencies require a new task.

Each run owns a private model client and private tool registry. Selected skill
providers are copied and installed only there. Manifest `tools` lists provider
additions, not an exhaustive allowlist: baseline tools such as `read_file`
remain available. No global provider install/uninstall state is changed. Hooks
are disabled for this profile. Saved permissions intersect current rules at
use; revocation and missing permission policy fail closed. Analyzer subprocesses
and tests use the normal ExecutionPolicy and consent gates. A review is not a
read-only execution capability and has no planning bypass.

Skill roots retain recursively registered asynchronous children. Normal
completion drains them; cancellation, truncation and exhausted budget cancel
them and retain the same drain through repeated cancellation. Parent text is
persisted while cleanup runs. The private client, approval gate, session lease
and inherited family remain owned until descendant cleanup completes. This
ownership extension is scoped to skill roots and scheduled roots; ordinary
Native asynchronous descendants keep their existing semantics.

An owned child must confirm its session and run admission before execution.
Missing storage or admission failure returns a tool failure without a child
model request. A child terminal write failure (including a missing run row)
cannot become successful completion evidence: the parent retains partial/review
evidence, and any unconfirmed child row remains unfinished for recovery.

Native requests use estimated reservation enforcement. Children, retries,
reviewed recovery and continuation share the original cumulative family, and
separate review dimensions have separate root ceilings plus the shared service
database daily ledger. Uncertain paid sends retain liability across restart.
Workspace review does not reconcile cost. Child admission failure prevents that
child launch; the parent may continue answering or using other tools while
retaining the failed-tool evidence. Child terminal persistence failure cannot
count as successful child completion. Budget-settlement persistence failure
retains the existing database-wide paid-admission quarantine.

Receipts preserve `skill_id`, text/output aliases, status, usage, tool count,
partial/error evidence and diff statistics before terminal state. Real model
review text has no invented structured findings or new parser. The offline
review path walks the actual submitted diff and labels its deterministic
output. A restart changes unfinished receipts to interrupted/partial without
resolving live dependencies or executing anything. Explicit reviewed resume
restores the profile and family; unknown accounting requires its existing
reconciliation flow.

The UI binds one selected Native session to both scoped Git diff and execution,
persists request keys before sending, retains run IDs, reloads stored receipts,
and supports cancel/open-run. Concurrent and delayed responses cannot overwrite
a newer run or another session. “Run all checks” admits three identifiable
independent roots (security, performance, style); failed/partial/paused results
stay visible. A single cumulative review batch remains future work.

No schema migration is needed: existing run request/metadata/result JSON fields
store profiles and receipts. SQLite online backups include them and the ledger.
The existing migration-037 JSON accounting/run-history import restriction still
applies; this change does not complete M7 restore compatibility.

Before rollback, stop active skill runs and retain an online SQLite snapshot
plus their ledger evidence. Older code does not enforce saved skill profiles;
do not resume/continue these runs with it. There is no automatic profile
downgrade. A database restore requires a matching application version and the
existing accounting reconciliation procedure; deleting profiles or budget
tables does not release paid liabilities.

## Offline verification (2026-10-26)

The frozen B1 batch passed 929 backend tests in 58 selected modules (310.55 s),
including actual SDK transport replay, SQLite receipts/families, recursive child
ownership, real OS process cleanup, scheduler and reviewed recovery. The exact
local selector and completed log are `.verification/skill-backend-selector-final.txt`
and `.verification/skill-frozen-backend.log`. Full frontend verification passed
838 tests in 107 files (42.50 s); TypeScript, ESLint, build, Ruff and diff checks
passed. The production build completed in 14.38 s with its existing chunk-size
advisory.

Four browser scenarios passed (21.6 s): three actual isolated-agent flows
(selected Git workspace + durable skill receipt/reload/timeline, normal durable
run recovery, and saved schedule context), plus one explicitly controlled HTTP
receipt replay for cancel interaction and partial/failed/budget-paused display.
The latter proves UI behavior; actual budget refusal, storage failures and
process cancellation are covered by backend tests. Model requests use offline
controlled SDK transports or forceMock; no paid model call was made. This is
scoped B1 evidence, not full M2/M5 production acceptance or live model-quality
validation.
