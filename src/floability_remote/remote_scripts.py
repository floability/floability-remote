"""Bash programs sent to the remote login node through standard input."""

PROBE = r"""
set -u

env_name=$1
requested_conda=$2

case "$requested_conda" in
    "~/"*) requested_conda="$HOME/${requested_conda#\~/}" ;;
esac

resolve_conda() {
    local candidate=$1
    local resolved=""
    local conda_base=""
    [ -n "$candidate" ] || return 1

    case "$candidate" in
        "~/"*) candidate="$HOME/${candidate#\~/}" ;;
    esac

    if [ -x "$candidate" ]; then
        printf '%s\n' "$candidate"
        return 0
    fi

    resolved=$(command -v "$candidate" 2>/dev/null || true)
    if [ -n "$resolved" ] && [ -x "$resolved" ]; then
        printf '%s\n' "$resolved"
        return 0
    fi

    # Conda is commonly a shell function. Resolve it to the stable executable
    # underneath its base installation so later SSH commands use the same
    # Conda even when shell initialization differs.
    conda_base=$("$candidate" info --base 2>/dev/null | tail -n 1 || true)
    if [ -n "$conda_base" ] && [ -x "$conda_base/bin/conda" ]; then
        printf '%s\n' "$conda_base/bin/conda"
        return 0
    fi
    return 1
}

find_conda() {
    if resolve_conda "$requested_conda"; then
        return
    fi

    if resolve_conda "${CONDA_EXE:-}"; then
        return
    fi

    if resolve_conda conda; then
        return
    fi

    for candidate in \
        "$HOME/.local/share/floability-remote/miniforge/bin/conda" \
        "$HOME/miniforge3/bin/conda" \
        "$HOME/mambaforge/bin/conda" \
        "$HOME/anaconda3/bin/conda" \
        "$HOME/miniconda3/bin/conda"; do
        if resolve_conda "$candidate"; then
            return
        fi
    done
}

conda_path=$(find_conda || true)
env_prefix=""
floability_version=""

if [ -n "$conda_path" ]; then
    # Resolve the named environment without `conda run`. Some HPC sites expose
    # environments correctly through `conda env list` but `conda run` fails in
    # a non-interactive SSH command even though it works in a login shell.
    env_listing=$("$conda_path" env list 2>/dev/null || true)
    env_prefix=$(printf '%s\n' "$env_listing" | awk -v target="$env_name" \
        '$1 == target { print $NF; exit }' || true)
    if [ -z "$env_prefix" ]; then
        # Prefix-created environments can be displayed without a name. Accept
        # a basename match only when it is unique; never guess between two
        # environments with the same final directory name.
        env_prefix=$(printf '%s\n' "$env_listing" | awk -v target="$env_name" '
            {
                path = $NF
                count_parts = split(path, parts, "/")
                if (parts[count_parts] == target) {
                    matches += 1
                    match_path = path
                }
            }
            END { if (matches == 1) print match_path }
        ' || true)
    fi
    if [ -n "$env_prefix" ] \
        && [ -x "$env_prefix/bin/python" ] \
        && [ -x "$env_prefix/bin/floability" ]; then
        floability_version=$("$env_prefix/bin/python" -c \
            'from importlib.metadata import version; print(version("floability"))' \
            2>/dev/null | tail -n 1 || true)
    fi
fi

downloader=""
if command -v curl >/dev/null 2>&1; then
    downloader="curl"
elif command -v wget >/dev/null 2>&1; then
    downloader="wget"
fi

printf '__FLOABILITY_REMOTE_OS__=%s\n' "$(uname -s 2>/dev/null || true)"
printf '__FLOABILITY_REMOTE_ARCH__=%s\n' "$(uname -m 2>/dev/null || true)"
printf '__FLOABILITY_REMOTE_CONDA__=%s\n' "$conda_path"
printf '__FLOABILITY_REMOTE_ENV_PREFIX__=%s\n' "$env_prefix"
printf '__FLOABILITY_REMOTE_VERSION__=%s\n' "$floability_version"
git_available=$(command -v git >/dev/null 2>&1 && printf yes || printf no)
setsid_available=$(command -v setsid >/dev/null 2>&1 && printf yes || printf no)
printf '__FLOABILITY_REMOTE_GIT__=%s\n' "$git_available"
printf '__FLOABILITY_REMOTE_SETSID__=%s\n' "$setsid_available"
printf '__FLOABILITY_REMOTE_DOWNLOADER__=%s\n' "$downloader"
"""


INSTALL_MINIFORGE = r"""
set -euo pipefail

architecture=$1
reinstall=$2
destination="$HOME/.local/share/floability-remote/miniforge"
work_dir="$HOME/.cache/floability-remote/bootstrap"

case "$architecture" in
    x86_64) installer=Miniforge3-Linux-x86_64.sh ;;
    aarch64|arm64) installer=Miniforge3-Linux-aarch64.sh ;;
    *)
        echo "Unsupported remote architecture for Miniforge: $architecture" >&2
        exit 2
        ;;
esac

backup=""

restore_previous_installation() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ]; then
        rm -rf -- "$destination"
        if [ -n "$backup" ] && [ -e "$backup" ]; then
            mv -- "$backup" "$destination"
            echo "Restored the previous managed Miniforge installation." >&2
        fi
    fi
    exit "$status"
}

if [ -e "$destination" ]; then
    if [ "$reinstall" = yes ]; then
        backup="${destination}.reinstall-backup.$$"
        if [ -e "$backup" ]; then
            echo "Temporary Miniforge backup path already exists: $backup" >&2
            exit 2
        fi
        mv -- "$destination" "$backup"
        trap restore_previous_installation EXIT
    elif [ -x "$destination/bin/conda" ]; then
        printf 'Miniforge already exists at %s\n' "$destination"
        exit 0
    else
        echo "Miniforge destination exists but is incomplete: $destination" >&2
        echo "Re-run with --reinstall-miniforge to replace it." >&2
        exit 2
    fi
fi

mkdir -p "$work_dir" "$(dirname "$destination")"
installer_path="$work_dir/$installer"
metadata_path="$work_dir/miniforge-release.json"
base_url="https://github.com/conda-forge/miniforge/releases/latest/download"
metadata_url="https://api.github.com/repos/conda-forge/miniforge/releases/latest"

if command -v curl >/dev/null 2>&1; then
    curl --fail --location --show-error --silent \
        --output "$installer_path" "$base_url/$installer"
    curl --fail --location --show-error --silent \
        --output "$metadata_path" "$metadata_url"
elif command -v wget >/dev/null 2>&1; then
    wget --quiet --output-document="$installer_path" "$base_url/$installer"
    wget --quiet --output-document="$metadata_path" "$metadata_url"
else
    echo "Neither curl nor wget is available on the remote host." >&2
    exit 2
fi

if ! command -v sha256sum >/dev/null 2>&1; then
    echo "sha256sum is required to verify the Miniforge installer." >&2
    exit 2
fi

expected_sha256=$(
    sed -n '
        /"name": "'"$installer"'"/,/"digest":/ {
            /"digest":/ {
                s/.*"digest": "sha256:\([0-9a-fA-F]*\)".*/\1/p
                q
            }
        }
    ' "$metadata_path"
)

if [ "${#expected_sha256}" -ne 64 ]; then
    echo "Could not find the Miniforge installer digest in GitHub release metadata." >&2
    exit 2
fi

actual_sha256=$(sha256sum "$installer_path" | awk '{print $1}')
if [ "$actual_sha256" != "$expected_sha256" ]; then
    echo "Miniforge installer checksum verification failed." >&2
    exit 2
fi

bash "$installer_path" -b -p "$destination"
rm -f -- "$installer_path" "$metadata_path"
"$destination/bin/conda" --version

if [ -n "$backup" ]; then
    rm -rf -- "$backup"
    backup=""
fi
trap - EXIT
"""


PREPARE_ENVIRONMENT = r"""
set -euo pipefail

conda_path=$1
env_name=$2
requested_version=$3
existing_prefix=$4

package_spec=floability
if [ -n "$requested_version" ]; then
    package_spec="floability=$requested_version"
fi

if [ -z "$existing_prefix" ]; then
    echo "Creating remote Conda environment '$env_name'..."
    "$conda_path" create -y -n "$env_name" \
        --channel conda-forge \
        --strict-channel-priority \
        python=3.12 "$package_spec"
else
    echo "Installing Floability in existing environment '$env_name'..."
    "$conda_path" install -y -n "$env_name" \
        --channel conda-forge \
        --strict-channel-priority \
        "$package_spec"
fi
"""


CLONE_BACKPACK = r"""
set -euo pipefail

remote_root=$1
run_id=$2
repository=$3
git_ref=$4

case "$remote_root" in
    "~/"*) remote_root="$HOME/${remote_root#\~/}" ;;
esac

run_dir="$remote_root/$run_id"
backpack_dir="$run_dir/backpack"
mkdir -p "$run_dir"
printf '%s\n' "$run_id" > "$run_dir/.floability-remote-run"

git clone -- "$repository" "$backpack_dir"
if [ -n "$git_ref" ]; then
    git -C "$backpack_dir" checkout --detach "$git_ref"
fi

printf '__FLOABILITY_REMOTE_RUN_DIR__=%s\n' "$run_dir"
printf '__FLOABILITY_REMOTE_BACKPACK__=%s\n' "$backpack_dir"
"""


LAUNCH_FLOABILITY = r"""
set -u

conda_path=$1
env_prefix=$2
run_dir=$3
backpack_dir=$4
mode=$5
batch_type=$6
jupyter_port=$7
entrypoint=$8
base_dir=$9
data_cache_dir=${10}
shift 10
# Remaining arguments are extra Floability options, already split into words.
extra_options=("$@")

expand_home() {
    case "$1" in
        "~") printf '%s\n' "$HOME" ;;
        "~/"*) printf '%s/%s\n' "$HOME" "${1#\~/}" ;;
        *) printf '%s\n' "$1" ;;
    esac
}

base_dir=$(expand_home "$base_dir")
data_cache_dir=$(expand_home "$data_cache_dir")

case "$mode" in
    run|execute) ;;
    *)
        echo "Unsupported Floability mode: $mode" >&2
        exit 2
        ;;
esac

state_file="$run_dir/floability.pid"
stdout_file="$run_dir/${mode}-command.log"

export CONDA_EXE="$conda_path"
export PATH="$env_prefix/bin:$PATH"
export PYTHONUNBUFFERED=1
export FLOABILITY_ACCESS_HOST=localhost

command=(
    "$env_prefix/bin/floability"
    "$mode"
    --backpack "$backpack_dir"
    --batch-type "$batch_type"
)
if [ "$mode" = run ]; then
    command+=(--jupyter-port "$jupyter_port")
fi
if [ -n "$entrypoint" ]; then
    command+=(--entrypoint "$entrypoint")
fi
if [ -n "$base_dir" ]; then
    command+=(--base-dir "$base_dir")
fi
if [ -n "$data_cache_dir" ]; then
    command+=(--data-cache-dir "$data_cache_dir")
fi
if [ "${#extra_options[@]}" -gt 0 ]; then
    command+=("${extra_options[@]}")
fi

echo "Remote run directory: $run_dir"
echo "Remote command: floability $mode --backpack <clone> --batch-type $batch_type"

# Bash starts background jobs with SIGINT ignored, and Python then never raises
# KeyboardInterrupt, so Floability could not clean up on SIGINT. Restore the
# default SIGINT action before exec. Do not use job control (set -m) instead:
# it makes the job a process-group leader, so util-linux setsid forks and $!
# would no longer be Floability's PID.
reset_sigint='import os, signal, sys
signal.signal(signal.SIGINT, signal.SIG_DFL)
os.execv(sys.argv[1], sys.argv[1:])'
setsid "$env_prefix/bin/python" -c "$reset_sigint" "${command[@]}" \
    > >(tee -a "$stdout_file") 2>&1 &
floability_pid=$!
printf '%s\n' "$floability_pid" > "$state_file"

set +e
wait "$floability_pid"
status=$?
set -e

rm -f -- "$state_file"
exit "$status"
"""


STOP_FLOABILITY = r"""
set -u

run_dir=$1
signal_name=$2
wait_seconds=$3
state_file="$run_dir/floability.pid"

if [ ! -f "$state_file" ]; then
    echo "No active remote PID file was found."
    exit 0
fi

pid=$(cat "$state_file")
case "$pid" in
    ''|*[!0-9]*)
        echo "Invalid PID file: $state_file" >&2
        exit 2
        ;;
esac

if ! kill -0 "$pid" 2>/dev/null; then
    rm -f -- "$state_file"
    echo "Remote Floability process has already stopped."
    exit 0
fi

echo "Sending SIG$signal_name to remote Floability process $pid..."
kill -s "$signal_name" "$pid"

deadline=$((SECONDS + wait_seconds))
while kill -0 "$pid" 2>/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
        printf '__FLOABILITY_REMOTE_STILL_RUNNING__=%s\n' "$pid"
        exit 3
    fi
    sleep 1
done

rm -f -- "$state_file"
echo "Remote Floability process stopped."
"""


CHECK_CLUSTER_STORAGE = r"""
set -u

base_dir=$1
case "$base_dir" in
    "~") base_dir="$HOME" ;;
    "~/"*) base_dir="$HOME/${base_dir#\~/}" ;;
esac

# `df` needs an existing path. Walk upward without creating the requested
# Floability base directory, which keeps this check read-only.
storage_path=$base_dir
while [ ! -e "$storage_path" ]; do
    parent=$(dirname -- "$storage_path")
    if [ "$parent" = "$storage_path" ]; then
        storage_path=""
        break
    fi
    storage_path=$parent
done

total_bytes=""
free_bytes=""
if [ -n "$storage_path" ]; then
    disk_line=$(df -Pk -- "$storage_path" 2>/dev/null | tail -n 1 || true)
    total_kib=$(printf '%s\n' "$disk_line" | awk '{print $2}')
    free_kib=$(printf '%s\n' "$disk_line" | awk '{print $4}')
    case "$total_kib:$free_kib" in
        *[!0-9:]*|:*) ;;
        *)
            total_bytes=$((total_kib * 1024))
            free_bytes=$((free_kib * 1024))
            ;;
    esac
fi

quota_status="unavailable"
quota_summary=""
if command -v quota >/dev/null 2>&1; then
    if command -v timeout >/dev/null 2>&1; then
        quota_exit=0
        quota_output=$(timeout 5 quota -s 2>&1) || quota_exit=$?
        quota_summary=$(printf '%s' "$quota_output" \
            | head -n 8 \
            | tr '\n\t' '  ' \
            | tr -s ' ' \
            | cut -c1-1000)
        if [ "$quota_exit" -eq 124 ]; then
            quota_status="timed-out"
        elif [ "$quota_exit" -ne 0 ]; then
            quota_status="error"
        elif [ -z "$(printf '%s' "$quota_output" | tr -d '[:space:]')" ] \
            || printf '%s' "$quota_output" | grep -Eqi 'no quota|none$|not enabled'; then
            quota_status="not-reported"
        else
            quota_status="reported"
        fi
    else
        quota_status="timeout-unavailable"
    fi
fi

printf '__FLOABILITY_REMOTE_CLUSTER_USER__=%s\n' "$(id -un 2>/dev/null || true)"
printf '__FLOABILITY_REMOTE_CLUSTER_HOST__=%s\n' "$(hostname -f 2>/dev/null || hostname 2>/dev/null || true)"
printf '__FLOABILITY_REMOTE_CLUSTER_BASE_DIR__=%s\n' "$base_dir"
printf '__FLOABILITY_REMOTE_CLUSTER_STORAGE_PATH__=%s\n' "$storage_path"
printf '__FLOABILITY_REMOTE_CLUSTER_TOTAL_BYTES__=%s\n' "$total_bytes"
printf '__FLOABILITY_REMOTE_CLUSTER_FREE_BYTES__=%s\n' "$free_bytes"
printf '__FLOABILITY_REMOTE_CLUSTER_QUOTA_STATUS__=%s\n' "$quota_status"
printf '__FLOABILITY_REMOTE_CLUSTER_QUOTA_SUMMARY__=%s\n' "$quota_summary"
"""


LIST_DOWNLOAD_FILES = r"""
set -euo pipefail

run_dir=$1
case "$run_dir" in
    "~") run_dir="$HOME" ;;
    "~/"*) run_dir="$HOME/${run_dir#\~/}" ;;
esac

find_python() {
    local candidate
    local recorded_python=""
    for candidate in "$run_dir/execute-command.log" "$run_dir/run-command.log"; do
        if [ -f "$candidate" ] && [ ! -L "$candidate" ]; then
            recorded_python=$(awk '
                sub(/^[[:space:]]*Python executable:[[:space:]]*/, "") {
                    print
                    exit
                }
            ' "$candidate")
            [ -n "$recorded_python" ] && break
        fi
    done
    for candidate in \
        "$recorded_python" \
        "$(command -v python3 2>/dev/null || true)" \
        "$(command -v python 2>/dev/null || true)" \
        "$HOME/.local/share/floability-remote/miniforge/bin/python" \
        "$HOME/miniforge3/bin/python" \
        "$HOME/miniconda3/bin/python"; do
        if [ -n "$candidate" ] && [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

python_path=$(find_python) || {
    echo "Python was not found on the remote system." >&2
    exit 3
}

"$python_path" - "$run_dir" <<'PY'
import json
import os
import stat
import sys
from pathlib import Path

run_dir = Path(sys.argv[1]).expanduser()
marker_file = run_dir / ".floability-remote-run"
if not run_dir.is_dir() or not marker_file.is_file() or marker_file.is_symlink():
    raise SystemExit("The path is not a Floability Remote run directory.")

limit = 2000
files = []
truncated = False


def add(group, logical_path, path):
    global truncated
    if len(files) >= limit:
        truncated = True
        return
    if any(ord(character) < 32 for character in logical_path):
        return
    try:
        information = path.lstat()
    except OSError:
        return
    if not stat.S_ISREG(information.st_mode) or path.is_symlink():
        return
    files.append(
        {
            "group": group,
            "path": logical_path,
            "remote_path": str(path),
            "size": information.st_size,
        }
    )


def add_tree(group, root, logical_root):
    global truncated
    if not root.is_dir() or root.is_symlink():
        return
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(
            name
            for name in directories
            if name not in {"vine-run-info", ".ipynb_checkpoints"}
            and not (current_path / name).is_symlink()
        )
        for name in sorted(names):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            add(group, f"{logical_root}/{relative}", path)
            if truncated:
                return


command_logs = []
for name in ("execute-command.log", "run-command.log"):
    path = run_dir / name
    try:
        if stat.S_ISREG(path.lstat().st_mode) and not path.is_symlink():
            command_logs.append(path)
            add("command", name, path)
    except OSError:
        pass

instance_dir = None
instance_marker = "[floability] Created instance structure at:"
for command_log in command_logs:
    try:
        with command_log.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if instance_marker in line:
                    candidate = line.split(instance_marker, 1)[1].strip()
                    path = Path(candidate).expanduser()
                    if path.is_absolute() and path.is_dir() and not path.is_symlink():
                        instance_dir = path
                    break
    except OSError:
        continue
    if instance_dir is not None:
        break

if instance_dir is not None and not truncated:
    add_tree("workflow", instance_dir / "workflow", "workflow")

if instance_dir is not None and not truncated:
    logs = instance_dir / "logs"
    if logs.is_dir() and not logs.is_symlink():
        for path in sorted(logs.iterdir(), key=lambda item: item.name):
            add("logs", f"logs/{path.name}", path)
            if truncated:
                break

if instance_dir is not None and not truncated:
    add("records", "catalog_update.json", instance_dir / "catalog_update.json")
    add_tree("records", instance_dir / "metadata", "metadata")
    if not truncated:
        add_tree("records", instance_dir / "metrics", "metrics")

files.sort(key=lambda item: (item["group"], item["path"]))
payload = {
    "python_path": sys.executable,
    "instance_found": instance_dir is not None,
    "truncated": truncated,
    "files": files,
}
print("__FLOABILITY_REMOTE_FILES__=" + json.dumps(payload, ensure_ascii=True))
PY
"""


DOWNLOAD_FILE = r"""
set -euo pipefail

remote_path=$1
maximum_size=$2
python_path=$3

if [ ! -x "$python_path" ]; then
    echo "The Python executable used for file validation is unavailable." >&2
    exit 3
fi

"$python_path" - "$remote_path" "$maximum_size" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
maximum_size = int(sys.argv[2])
flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)

try:
    descriptor = os.open(path, flags)
except OSError as error:
    raise SystemExit(f"Could not open the selected file: {error}")

try:
    information = os.fstat(descriptor)
    if not stat.S_ISREG(information.st_mode):
        raise SystemExit("The selected path is no longer a regular file.")
    if information.st_size > maximum_size:
        raise SystemExit(
            f"The selected file is {information.st_size} bytes; "
            f"the limit is {maximum_size} bytes."
        )

    remaining = maximum_size + 1
    output = sys.stdout.buffer
    while remaining:
        chunk = os.read(descriptor, min(1024 * 1024, remaining))
        if not chunk:
            break
        output.write(chunk)
        remaining -= len(chunk)
    output.flush()
    if remaining == 0 and os.read(descriptor, 1):
        raise SystemExit("The selected file grew beyond the download limit.")
finally:
    os.close(descriptor)
PY
"""


IDENTIFY = r"""
set -u

printf '__FLOABILITY_REMOTE_USER__=%s\n' "$(id -un 2>/dev/null || whoami)"
printf '__FLOABILITY_REMOTE_HOST__=%s\n' "$(hostname 2>/dev/null || uname -n)"
"""


ALL = (
    IDENTIFY,
    PROBE,
    INSTALL_MINIFORGE,
    PREPARE_ENVIRONMENT,
    CLONE_BACKPACK,
    LAUNCH_FLOABILITY,
    STOP_FLOABILITY,
    CHECK_CLUSTER_STORAGE,
    LIST_DOWNLOAD_FILES,
    DOWNLOAD_FILE,
)
