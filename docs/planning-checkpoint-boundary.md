# Automatic checkpoint startup boundary

Native planning runs skip the entire automatic baseline checkpoint path. They
do not launch the Git eligibility probe, create snapshot directories or call
the checkpoint manager. Planning receives no additional process capability.

Native chat and execute runs keep the existing advisory baseline behavior.
Their Git eligibility probe uses the shared managed execution boundary with
the accepted workspace as its working directory, a filtered environment,
bounded output (4 KiB per stream) and a five-second deadline. It runs
`git --no-pager -c core.fsmonitor=false rev-parse --git-dir`; a missing bound
directory cannot fall back to the service's working directory.

A non-Git directory, missing Git, probe timeout or snapshot failure leaves the
run eligible to continue without a baseline. Cancellation propagates after the
managed process and descendants have been reaped. Snapshot contents retain the
checkpoint manager's existing semantics; a baseline is recovery evidence and
does not promise exact per-run undo.

The offline tests exercise native planning with process and snapshot tripwires,
real Git baselines for chat and execute, non-Git and missing-Git behavior,
synthetic environment filtering, a missing workspace, and real local process
trees for probe timeout and cancellation. No model or remote service is used.

The isolated change passed 116 related tests (one existing AsyncMock transaction
warning). After integration with Schema037, seven related backend modules passed
148 tests in 92.49 seconds. Evidence is `.verification/checkpoint-budget-integration.log`;
the isolated logs are retained under `.verification/planning-checkpoint-b7630a4/`.
The main-checkout Ruff run required a test import-order correction and then passed.
This is local offline verification, not full production or remote acceptance.
