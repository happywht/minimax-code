#!/usr/bin/env bash
# Interactive offline data-only bundle operations for an installed release.
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
agent_dir=$(CDPATH= cd -- "$script_dir/../agent" && pwd -P)
python="$agent_dir/.venv/bin/python"

if [ ! -x "$python" ]; then
    printf '%s' 'Absolute path to Python 3.11+ with this release dependencies installed: '
    IFS= read -r python || exit 1
fi
case "$python" in /*) ;; *) printf '%s\n' 'Python path must be absolute.' >&2; exit 1 ;; esac
if [ ! -f "$python" ] || [ ! -x "$python" ] || [ ! -f "$agent_dir/minimax_code/bundle.py" ]; then
    printf '%s\n' 'Python executable or this release bundle module is missing.' >&2
    exit 1
fi

printf '%s\n' \
    'Data-only bundle: registered data supported by this release; not a full application backup.' \
    'Workspaces, keyring, environment and other external files are excluded. The database may contain credentials.' \
    'Stop related services and tasks before creation or recovery; independently verify the source and protect the files.' \
    'A restored directory is a copy requiring reconciliation; it is not started or rebound by this tool.'
printf '%s' 'C = create, V = verify, R = restore to a new directory, Enter = cancel: '
IFS= read -r choice || exit 1
case "$choice" in
    C|c) command=create; source_label='Stopped source data directory'; target_label='New output .zip path' ;;
    V|v) command=verify; source_label='Trusted bundle .zip path' ;;
    R|r) command=restore; source_label='Trusted bundle .zip path'; target_label='New data directory' ;;
    '') printf '%s\n' 'Cancelled.'; exit 0 ;;
    *) printf '%s\n' 'Invalid choice.' >&2; exit 1 ;;
esac
printf '%s: ' "$source_label"
IFS= read -r source || exit 1
case "$source" in /*) ;; *) printf '%s\n' 'Source must be an absolute path.' >&2; exit 1 ;; esac
if [ "$command" != verify ]; then
    printf '%s: ' "$target_label"
    IFS= read -r target || exit 1
    case "$target" in /*) ;; *) printf '%s\n' 'Target must be an absolute path.' >&2; exit 1 ;; esac
    printf 'Source: %s\nNew target: %s\n' "$source" "$target"
else
    printf 'Bundle: %s\n' "$source"
fi
printf '%s' 'Enter YES to proceed, or Enter to cancel: '
IFS= read -r confirm || exit 1
if [ "$confirm" != YES ]; then printf '%s\n' 'Cancelled.'; exit 0; fi

cd -- "$agent_dir"
case "$command" in
    create) "$python" -m minimax_code.bundle create --data-dir "$source" --output "$target" ;;
    verify) "$python" -m minimax_code.bundle verify --bundle "$source" ;;
    restore) "$python" -m minimax_code.bundle restore --bundle "$source" --new-data-dir "$target" ;;
esac
printf '%s\n' 'Review the receipt, exclusions and recovery instructions before using the result.'
