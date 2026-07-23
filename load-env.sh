#!/bin/zsh

ENV_FILE="$HOME/src/camp_scanner/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Error: $ENV_FILE not found"
    return 1 2>/dev/null || exit 1
fi

set -a
source "$ENV_FILE"
set +a

echo "Yosemite environment variables loaded."