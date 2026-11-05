#!/bin/sh
set -eu
: "${CI_COMMIT_TAG:?Version tag is required}"
: "${DOCKER_USERNAME:?Registry user is required}"
: "${DOCKER_PASSWORD:?Registry password is required}"
REGISTRY=${REGISTRY:-docker-hub.f123.pub}
IMAGE_REPO=${IMAGE_REPO:-docker-hub.f123.pub/lfun/explanation}
umask 077
mkdir -p /kaniko/.docker
trap 'rm -f /kaniko/.docker/config.json' EXIT
AUTH=$(printf '%s:%s' "$DOCKER_USERNAME" "$DOCKER_PASSWORD" | base64 | tr -d '\n')
printf '{"auths":{"%s":{"auth":"%s"}}}\n' "$REGISTRY" "$AUTH" > /kaniko/.docker/config.json
unset AUTH DOCKER_PASSWORD
/kaniko/executor \
  --dockerfile=Dockerfile \
  --context="dir://${CI_WORKSPACE:-/woodpecker/src}" \
  --destination="$IMAGE_REPO:$CI_COMMIT_TAG" \
  --destination="$IMAGE_REPO:$CI_COMMIT_TAG-amd64" \
  --custom-platform=linux/amd64 \
  --label="org.opencontainers.image.revision=${CI_COMMIT_SHA:-unknown}" \
  --cache=true --cache-repo="$IMAGE_REPO-cache-amd64"
