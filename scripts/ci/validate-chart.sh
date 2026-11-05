#!/bin/sh
set -eu
CHECK_TMP=$(mktemp -d)
trap 'rm -rf "$CHECK_TMP"' EXIT
for ENVIRONMENT in test prod; do
  if [ "$ENVIRONMENT" = test ]; then VALUES=deploy/values.yaml; else VALUES=deploy/values-prod.yaml; fi
  helm lint deploy/chart -f "$VALUES" --set-string image.tag="$ENVIRONMENT-v0.1.0" --set-string ingress.host=courseware.example.net
  helm template explanation deploy/chart -f "$VALUES" \
    --set-string image.tag="$ENVIRONMENT-v0.1.0" --set-string ingress.host=courseware.example.net > "$CHECK_TMP/$ENVIRONMENT.yaml"
done
# Missing a release version or enabled Ingress hostname must fail before deployment.
if helm template explanation deploy/chart > "$CHECK_TMP/invalid.yaml" 2>/dev/null; then
  echo 'Chart accepted an empty image tag' >&2; exit 1
fi
if helm template explanation deploy/chart -f deploy/values.yaml --set-string image.tag=test-v0.1.0 --set-string ingress.host= > "$CHECK_TMP/invalid.yaml" 2>/dev/null; then
  echo 'Chart accepted an empty Ingress hostname' >&2; exit 1
fi
helm template explanation deploy/chart --set-string image.tag=test-v0.1.0 \
  --set-string persistence.existingClaim=existing-courseware-data > "$CHECK_TMP/existing.yaml"
if grep -q '^kind: PersistentVolumeClaim$' "$CHECK_TMP/existing.yaml"; then
  echo 'Existing claim configuration unexpectedly creates a PVC' >&2; exit 1
fi
echo 'Test/prod charts and required deployment inputs validated.'
