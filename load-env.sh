#!/bin/zsh


ENV_FILE="${CAMP_SCANNER_ENV_FILE:-${0:A:h}/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Error: $ENV_FILE not found" >&2
    return 1 2>/dev/null || exit 1
fi

set -a
source "$ENV_FILE"
set +a

echo "Camp scanner environment variables loaded from $ENV_FILE."