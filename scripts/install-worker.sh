#!/usr/bin/env bash
# Bundled, fixed installer. No shell profiles or system Python are changed.
set -euo pipefail
umask 077
payload_dir=$(cd -- "$(dirname -- "$0")" && pwd)
install_dir=${VENTURI_INSTALL_DIR:-"${XDG_DATA_HOME:-$HOME/.local/share}/venturi"}
# OpenFOAM rejects whitespace and several non-ASCII/shell characters in its
# runtime and case paths. Reject them before downloading or touching an install.
if [[ ! "$install_dir" =~ ^/[a-zA-Z0-9_./-]+$ ]]; then
  echo 'Choose an absolute worker installation path containing only ASCII letters, digits, /, ., _, or -. OpenFOAM cannot use spaces or other characters in this path.' >&2
  exit 1
fi
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo 'Use Ubuntu 22.04 x86_64, including Windows WSL2.' >&2
  exit 1
fi
if ! /usr/bin/grep -qx 'VERSION_ID="22.04"' /etc/os-release || ! /usr/bin/grep -qx 'ID=ubuntu' /etc/os-release; then
  echo 'The worker requires Ubuntu 22.04. Choose Ubuntu-22.04 in WSL.' >&2
  exit 1
fi
cd "$payload_dir"
sha256sum --check --strict SHA256SUMS
mkdir -p "$install_dir"
exec 9>"$install_dir/setup.lock"
flock -n 9 || { echo 'Another installation is already running.' >&2; exit 1; }
exec 8>"$install_dir/.host.lock"
flock -n 8 || { echo 'Worker startup is in progress. Retry setup shortly.' >&2; exit 1; }
mkdir -p "$install_dir/projects"
exec 7>"$install_dir/projects/.admission.lock"
flock -n 7 || { echo 'A simulation is being submitted. Retry setup shortly.' >&2; exit 1; }
release_id=$(cat VERSION)
[[ "$release_id" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 1
mkdir -p "$install_dir/releases" "$install_dir/python"
export UV_PYTHON_INSTALL_DIR="$install_dir/python"
export UV_PYTHON_PREFERENCE=only-managed
export UV_NO_MODIFY_PATH=1
export UV_CACHE_DIR="$install_dir/download-cache"
echo 'Installing the private Python runtime…'
"$payload_dir/uv" python install 3.12.14
bootstrap_python=$("$payload_dir/uv" python find 3.12.14)
"$bootstrap_python" - "$install_dir" <<'MAINTENANCE'
import json, os, signal, sys, time
from pathlib import Path
root = Path(sys.argv[1])
for path in (root / "projects/runs").glob("*/status.json"):
    if json.loads(path.read_text()).get("status") in {"queued", "running"}:
        sys.exit("Finish or cancel active simulations before installing or repairing the runtime.")
for path in (root / "projects/campaigns").glob("*/status.json"):
    if json.loads(path.read_text()).get("status") in {"queued", "running", "starting", "stopping"}:
        sys.exit("Finish or cancel the recovery campaign before updating.")
session = root / "worker-session.json"
if session.exists():
    identity = json.loads(session.read_text()).get("process")
    def alive():
        if not identity:
            return False
        try:
            stat = Path(f"/proc/{identity['pid']}/stat").read_text().rsplit(")", 1)[1].split()
            return (stat[0] != "Z" and stat[19] == identity["start_ticks"] and
                    Path("/proc/sys/kernel/random/boot_id").read_text().strip() == identity["boot_id"])
        except (OSError, KeyError):
            return False
    if alive():
        os.kill(identity["pid"], signal.SIGTERM)
        for _ in range(100):
            if not alive():
                break
            time.sleep(.1)
        else:
            # Admission is locked and no simulations/campaigns are active. An API
            # request waiting for that lock may delay graceful HTTP shutdown.
            if alive():
                os.kill(identity["pid"], signal.SIGKILL)
            for _ in range(50):
                if not alive():
                    break
                time.sleep(.1)
            else:
                sys.exit("The idle API worker did not stop. Close it before updating.")
MAINTENANCE
# Never synchronize dependencies or extract a solver into the active release.
# Keep this final path stable: virtual-environment entry points embed its name.
release_dir=$(mktemp -d "$install_dir/releases/$release_id.XXXXXXXX")
published=0
trap 'if [[ "$published" == 0 ]]; then rm -rf -- "$release_dir"; fi' EXIT
"$payload_dir/uv" venv --python 3.12.14 "$release_dir"
echo 'Installing the locked application and CAD dependencies…'
"$payload_dir/uv" pip sync --python "$release_dir/bin/python" --require-hashes "$payload_dir/requirements.txt"
"$payload_dir/uv" pip install --python "$release_dir/bin/python" --no-deps "$payload_dir/venturi_workbench-$release_id-py3-none-any.whl"
echo 'Installing and checking the pinned simulation runtime…'
mkdir -p "$release_dir/solver" "$install_dir/solver/downloads"
ln -s "$install_dir/solver/downloads" "$release_dir/solver/downloads"
"$release_dir/bin/python" -m venturi.installation --destination "$release_dir/solver"
cat > "$release_dir/start-worker" <<'START'
#!/usr/bin/env bash
set -euo pipefail
umask 077
release_dir=$(cd -- "$(dirname -- "$0")" && pwd)
install_dir=$(cd -- "$release_dir/../.." && pwd)
export VENTURI_FOAM_ROOT="$release_dir/solver/runtime/opt/openfoam14"
if [[ -f "$install_dir/managed-url" ]]; then
  export VENTURI_MANAGED_URL=$(cat "$install_dir/managed-url")
fi
exec "$release_dir/bin/python" -m venturi.cli serve --storage "$install_dir/projects" "$@"
START
chmod 700 "$release_dir/start-worker"
if [[ -f "$payload_dir/managed-url" ]]; then cp "$payload_dir/managed-url" "$install_dir/managed-url"; fi
ln -sfn "$release_dir" "$install_dir/current.next"
mv -Tf "$install_dir/current.next" "$install_dir/current"
published=1
echo 'Venturi worker is installed. Start the worker from the desktop.'
