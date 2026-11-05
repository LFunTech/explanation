#!/bin/sh
set -eu
apk add --no-cache curl python3
TOOLS_TMP=$(mktemp -d)
trap 'rm -rf "$TOOLS_TMP"' EXIT
# 与 school-oms 使用同一个工具镜像源。
curl -fsSL --retry 3 https://minio.f123.pub/k8s/kubectl -o "$TOOLS_TMP/kubectl"
curl -fsSL --retry 3 https://minio.f123.pub/k8s/helm-v3.17.3-linux-amd64.tar.gz -o "$TOOLS_TMP/helm.tar.gz"
tar -xzf "$TOOLS_TMP/helm.tar.gz" -C "$TOOLS_TMP"
install -m 755 "$TOOLS_TMP/kubectl" /usr/local/bin/kubectl
install -m 755 "$TOOLS_TMP/linux-amd64/helm" /usr/local/bin/helm
helm version --short
