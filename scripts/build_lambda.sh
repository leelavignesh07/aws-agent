#!/usr/bin/env bash
# Build the Lambda deployment package — bash and python only, no Docker (STAGE 4).
#
# A Lambda zip has no AWS CLI, and the agent collects everything by shelling out
# to `aws`. So the package carries its own copy: the pip-installable AWS CLI v1,
# plus a tiny `bin/aws` shim that runs it with the package on PYTHONPATH. The
# function is pointed at that shim with AGENT_AWS_BIN.
#
# (AWS CLI v2 is not an option here: its unpacked install is ~270 MB, over
# Lambda's 250 MB limit. Locally you still want v2 — `make install-awscli`.)
#
# Cross-building is handled by pip's --platform/--python-version, so this
# produces a Linux package on macOS as well.
#
#   scripts/build_lambda.sh              -> dist/agent-lambda.zip
#   ARCH=arm64 scripts/build_lambda.sh   -> arm64 package
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="${ROOT}/dist"
BUILD="${DIST}/lambda"
ZIP="${DIST}/agent-lambda.zip"

PYTHON="${PYTHON:-python3}"
PY_VERSION="${PY_VERSION:-3.12}"          # must match the Lambda runtime
ARCH="${ARCH:-x86_64}"
AWSCLI_SPEC="${AWSCLI_SPEC:-awscli>=1.32,<2}"

case "${ARCH}" in
  x86_64)        PLATFORM="manylinux2014_x86_64" ;;
  arm64|aarch64) PLATFORM="manylinux2014_aarch64" ;;
  *) echo "unsupported ARCH: ${ARCH} (use x86_64 or arm64)" >&2; exit 1 ;;
esac

command -v "${PYTHON}" >/dev/null || { echo "${PYTHON} is required but not on PATH" >&2; exit 1; }

echo "==> [1/5] cleaning ${BUILD}"
rm -rf "${BUILD}" "${ZIP}"
mkdir -p "${BUILD}"

echo "==> [2/5] installing dependencies for linux/${ARCH} python ${PY_VERSION}"
"${PYTHON}" -m pip install \
  --quiet --disable-pip-version-check \
  --target "${BUILD}" \
  --platform "${PLATFORM}" \
  --implementation cp \
  --python-version "${PY_VERSION}" \
  --only-binary=:all: \
  -r "${ROOT}/requirements.txt" "${AWSCLI_SPEC}"

echo "==> [3/5] adding the agent and the knowledge base"
cp -R "${ROOT}/agent" "${BUILD}/agent"
cp -R "${ROOT}/knowledge" "${BUILD}/knowledge"

# pip's console scripts carry the build machine's shebang, which is wrong
# everywhere else. Replace them with one shim that finds its own package root.
rm -rf "${BUILD}/bin"
mkdir -p "${BUILD}/bin"
cat > "${BUILD}/bin/aws" <<'SHIM'
#!/bin/sh
# AWS CLI entry point for the packaged agent. Self-locating: works at
# /var/task/bin/aws in Lambda and out of dist/lambda/bin/aws on a laptop.
root="$(cd "$(dirname "$0")/.." && pwd)"
PYTHONPATH="${root}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONPATH
exec "${AGENT_PACKAGE_PYTHON:-python3}" -c 'import sys; from awscli.clidriver import main; sys.exit(main())' "$@"
SHIM
chmod 755 "${BUILD}/bin/aws"

find "${BUILD}" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "${BUILD}" -name '*.pyc' -delete 2>/dev/null || true

echo "==> [4/5] zipping"
"${PYTHON}" - "${BUILD}" "${ZIP}" <<'PYZIP'
import os
import stat
import sys
import zipfile

build, target = sys.argv[1], sys.argv[2]
# A fixed timestamp keeps the zip byte-identical between builds of the same
# tree, so Terraform does not redeploy a package that has not changed.
fixed_time = (2000, 1, 1, 0, 0, 0)

with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for directory, dirnames, filenames in os.walk(build):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(directory, name)
            if os.path.islink(path):
                continue
            arcname = os.path.relpath(path, build)
            mode = stat.S_IMODE(os.stat(path).st_mode)
            info = zipfile.ZipInfo(arcname, date_time=fixed_time)
            # Lambda unzips with these bits, so the aws shim has to stay +x.
            info.external_attr = (mode & 0o7777) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(path, "rb") as fh:
                zf.writestr(info, fh.read())
PYZIP

echo "==> [5/5] checking the package against Lambda's limits"
"${PYTHON}" - "${BUILD}" "${ZIP}" <<'PYCHECK'
import os
import sys

build, target = sys.argv[1], sys.argv[2]
unpacked = sum(
    os.path.getsize(os.path.join(d, f))
    for d, _, files in os.walk(build)
    for f in files
    if not os.path.islink(os.path.join(d, f))
)
packed = os.path.getsize(target)
mb = 1024 * 1024
print(f"    {target}")
print(f"    zipped   {packed / mb:6.1f} MB   (direct upload limit 50 MB)")
print(f"    unpacked {unpacked / mb:6.1f} MB   (Lambda limit 250 MB)")

failed = False
if packed > 50 * mb:
    print("    FAIL: too large to upload directly — deploy through S3", file=sys.stderr)
    failed = True
if unpacked > 250 * mb:
    print("    FAIL: over Lambda's unzipped size limit", file=sys.stderr)
    failed = True
sys.exit(1 if failed else 0)
PYCHECK

echo "==> package ready"
