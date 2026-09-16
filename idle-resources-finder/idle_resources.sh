#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
BOOTSTRAP_PY="${PYTHON:-python}"

if [ ! -d "$VENV_DIR" ]; then
    echo "Initializing..."
    "$BOOTSTRAP_PY" -m venv "$VENV_DIR"
fi

if [ -x "$VENV_DIR/Scripts/python.exe" ]; then
    VENV_PY="$VENV_DIR/Scripts/python.exe"
else
    VENV_PY="$VENV_DIR/bin/python"
fi

if ! "$VENV_PY" -c "import boto3" >/dev/null 2>&1; then
    "$VENV_PY" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt"
fi

arrow_select() {
    local options=("$@") selected=0 key
    tput civis >&2 2>/dev/null
    while true; do
        for i in "${!options[@]}"; do
            if [ "$i" -eq "$selected" ]; then
                printf '> %s\n' "${options[$i]}" >&2
            else
                printf '  %s\n' "${options[$i]}" >&2
            fi
        done
        IFS= read -rsn1 key
        if [[ "$key" == $'\x1b' ]]; then
            read -rsn2 -t 0.1 key
            [[ "$key" == "[A" ]] && selected=$(( (selected - 1 + ${#options[@]}) % ${#options[@]} ))
            [[ "$key" == "[B" ]] && selected=$(( (selected + 1) % ${#options[@]} ))
        elif [[ -z "$key" ]]; then
            break
        fi
        tput cuu "${#options[@]}" >&2
    done
    tput cnorm >&2 2>/dev/null
    printf '%s\n' "${options[$selected]}"
}

echo "Region mode (use Up/Down, Enter to confirm):"
MODE=$(arrow_select "Specific region(s)" "All enabled regions (--all-regions)")

ARGS=()
if [ "$MODE" = "All enabled regions (--all-regions)" ]; then
    ARGS+=(--all-regions)
else
    read -rp "How many regions? (ENTER for default - 1): " N
    N="${N:-1}"
    for i in $(seq 1 "$N"); do
        read -rp "Region #$i (e.g. eu-west-1): " REGION
        ARGS+=(--region "$REGION")
    done
fi

read -rp "EC2 stopped threshold in days (ENTER for default - 14): " DAYS
DAYS="${DAYS:-14}"
ARGS+=(--ec2-stopped-days "$DAYS")

read -rp "Output file prefix (ENTER for default - idle): " OUTNAME
OUTNAME="${OUTNAME:-idle}"
ARGS+=(--output "$OUTNAME")

"$VENV_PY" "$SCRIPT_DIR/idle_resources.py" "${ARGS[@]}" --output-dir "$SCRIPT_DIR/export"
