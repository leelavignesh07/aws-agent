#!/usr/bin/env bash
# Install AWS CLI v2 into ~/.local — no root required (STAGE 1).
set -euo pipefail

if command -v aws >/dev/null 2>&1; then
  echo "==> aws is already installed: $(aws --version 2>&1)"
  exit 0
fi

OS="$(uname -s)"
ARCH="$(uname -m)"
PREFIX="${HOME}/.local"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

case "${OS}" in
  Linux)
    case "${ARCH}" in
      x86_64)          URL="https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" ;;
      aarch64|arm64)   URL="https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" ;;
      *) echo "unsupported architecture: ${ARCH}" >&2; exit 1 ;;
    esac
    echo "==> downloading AWS CLI v2 for ${OS}/${ARCH}"
    curl -fsSL "${URL}" -o "${TMP}/awscliv2.zip"
    command -v unzip >/dev/null || { echo "unzip is required — install it (apt install unzip / dnf install unzip)"; exit 1; }
    unzip -q "${TMP}/awscliv2.zip" -d "${TMP}"
    "${TMP}/aws/install" --bin-dir "${PREFIX}/bin" --install-dir "${PREFIX}/aws-cli" --update
    ;;
  Darwin)
    echo "==> downloading AWS CLI v2 for macOS"
    curl -fsSL "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o "${TMP}/AWSCLIV2.pkg"
    echo "    installing (this needs your password)"
    sudo installer -pkg "${TMP}/AWSCLIV2.pkg" -target /
    ;;
  *)
    echo "unsupported OS: ${OS}. Install AWS CLI v2 manually:" >&2
    echo "  https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html" >&2
    exit 1
    ;;
esac

if ! command -v aws >/dev/null 2>&1; then
  echo ""
  echo "==> installed to ${PREFIX}/bin, which is not on your PATH yet. Add it:"
  echo "      echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc && source ~/.bashrc"
else
  echo "==> $(aws --version 2>&1)"
fi
