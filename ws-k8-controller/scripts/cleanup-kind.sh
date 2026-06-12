#!/usr/bin/env bash
set -euo pipefail

# cleanup-kind.sh
# Delete kind clusters safely. If no args provided, attempts to delete
# a small set of commonly-used experiment cluster names.

DEFAULT_CLUSTERS=(stateful-exp exp-failure)

if [ "$#" -ge 1 ]; then
  CLUSTERS=("$@")
else
  CLUSTERS=("${DEFAULT_CLUSTERS[@]}")
fi

for name in "${CLUSTERS[@]}"; do
  echo "Checking kind cluster: $name"
  if kind get clusters | grep -q "^${name}$"; then
    echo "Deleting kind cluster: $name"
    kind delete cluster --name "$name"
  else
    echo "Cluster '$name' not present, skipping."
  fi
done

echo "Done."
