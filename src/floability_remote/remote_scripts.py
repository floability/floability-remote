"""Bash programs sent to the remote login node through standard input."""

PROBE = r"""
set -u

env_name=$1
requested_conda=$2

case "$requested_conda" in
    "~/"*) requested_conda="$HOME/${requested_conda#\~/}" ;;
esac

find_conda() {
    if [ -n "$requested_conda" ] && [ -x "$requested_conda" ]; then
        printf '%s\n' "$requested_conda"
        return
    fi

    if command -v conda >/dev/null 2>&1; then
        command -v conda
        return
    fi

    for candidate in \
        "$HOME/.local/share/floability-remote/miniforge/bin/conda" \
        "$HOME/miniforge3/bin/conda" \
        "$HOME/mambaforge/bin/conda" \
        "$HOME/anaconda3/bin/conda" \
        "$HOME/miniconda3/bin/conda"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return
        fi
    done
}

conda_path=$(find_conda || true)
env_prefix=""
floability_version=""

if [ -n "$conda_path" ]; then
    env_prefix=$("$conda_path" run -n "$env_name" python -c \
        'import sys; print(sys.prefix)' 2>/dev/null | tail -n 1 || true)
    if [ -n "$env_prefix" ] && [ -x "$env_prefix/bin/floability" ]; then
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
)
