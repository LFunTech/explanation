#!/bin/sh
set -eu
: "${CI_COMMIT_TAG:?Version tag is required}"
: "${DEPLOY_ENV:?test or prod is required}"
: "${KUBECONFIG_DATA:?Cluster configuration is required}"
: "${APP_CONFIG:?Runtime configuration is required}"
: "${DOCKER_USERNAME:?Registry user is required}"
: "${DOCKER_PASSWORD:?Registry password is required}"
case "$DEPLOY_ENV:$CI_COMMIT_TAG" in
  test:test-v*) NAMESPACE=explanation-test; VALUES=deploy/values.yaml ;;
  prod:prod-v*) NAMESPACE=explanation; VALUES=deploy/values-prod.yaml ;;
  *) echo 'Tag does not match the deployment environment' >&2; exit 1 ;;
esac
RELEASE_NAME=explanation
IMAGE_REPO=${IMAGE_REPO:-docker-hub.f123.pub/lfun/explanation}
umask 077
DEPLOY_TMP=$(mktemp -d)
trap 'rm -rf "$DEPLOY_TMP"' EXIT
export KUBECONFIG="$DEPLOY_TMP/kubeconfig"
python3 scripts/ci/write_kubeconfig.py "$KUBECONFIG"
python3 scripts/ci/prepare_config.py "$DEPLOY_TMP"
unset APP_CONFIG KUBECONFIG_DATA
PUBLIC_HOST=$(cat "$DEPLOY_TMP/host")
CONFIG_CHECKSUM=$(sha256sum "$DEPLOY_TMP/runtime.env" | cut -d ' ' -f 1)
# Render/validate the chart before any cluster mutations.
helm lint deploy/chart -f "$VALUES" --set-string image.tag="$CI_COMMIT_TAG" --set-string ingress.host="$PUBLIC_HOST"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml > "$DEPLOY_TMP/namespace.yaml"
kubectl apply -f "$DEPLOY_TMP/namespace.yaml"
kubectl -n "$NAMESPACE" create secret generic explanation-config \
  --from-env-file="$DEPLOY_TMP/runtime.env" --dry-run=client -o yaml > "$DEPLOY_TMP/config.yaml"
kubectl -n "$NAMESPACE" apply -f "$DEPLOY_TMP/config.yaml"
# Registry auth is sent only to the deployment namespace, never to an image layer.
python3 - "$DEPLOY_TMP/dockerconfig.json" <<'PY'
import base64, json, os, sys
from pathlib import Path
auth = base64.b64encode((os.environ['DOCKER_USERNAME'] + ':' + os.environ['DOCKER_PASSWORD']).encode()).decode()
Path(sys.argv[1]).write_text(json.dumps({'auths': {'docker-hub.f123.pub': {'auth': auth}}}))
PY
unset DOCKER_PASSWORD
kubectl -n "$NAMESPACE" create secret generic lfun --type=kubernetes.io/dockerconfigjson \
  --from-file=".dockerconfigjson=$DEPLOY_TMP/dockerconfig.json" --dry-run=client -o yaml > "$DEPLOY_TMP/pull-secret.yaml"
kubectl -n "$NAMESPACE" apply -f "$DEPLOY_TMP/pull-secret.yaml"
if ! helm upgrade --install "$RELEASE_NAME" deploy/chart \
  --namespace "$NAMESPACE" -f "$VALUES" \
  --set-string image.repository="$IMAGE_REPO" \
  --set-string image.tag="$CI_COMMIT_TAG" \
  --set-string ingress.host="$PUBLIC_HOST" \
  --set-string runtimeConfigChecksum="$CONFIG_CHECKSUM" \
  --wait --timeout 10m --history-max 10; then
  kubectl -n "$NAMESPACE" get pods,pvc,ingress || true
  kubectl -n "$NAMESPACE" get events --sort-by=.lastTimestamp | tail -30 || true
  exit 1
fi
kubectl -n "$NAMESPACE" rollout status deployment/"$RELEASE_NAME" --timeout=60s
echo "Deployment ready: $RELEASE_NAME ($DEPLOY_ENV / $CI_COMMIT_TAG)"
