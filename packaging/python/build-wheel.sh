#!/usr/bin/env bash
# Builds the rsid-py wheel for linux arm64 (cp312, manylinux_2_28).
# Meant to run inside a manylinux_2_28_aarch64 container (which provides /opt/python/cp312-cp312),
# invoked by .github/workflows/python-package.yml. To run locally from the repo root:
#
#   mkdir -p dist && docker run --rm -v "$(pwd)":/src:ro -v "$(pwd)/dist":/src/dist \
#       quay.io/pypa/manylinux_2_28_aarch64 bash /src/packaging/python/build-wheel.sh
#
set -euo pipefail

PY=/opt/python/cp312-cp312/bin/python
PYENV=/opt/python/cp312-cp312

# RSID_PY_SECURE=1 builds the secure variant (adds pair / secure session, F45x only, mbedtls inside)
SECURE_CMAKE_FLAG=""
PKG_NAME=rsid-py
if [ "${RSID_PY_SECURE:-0}" = "1" ]; then
    SECURE_CMAKE_FLAG="-DRSID_SECURE=ON"
    PKG_NAME=rsid-py-secure
fi

# recent cmake + auditwheel from pip; setuptools/wheel for the packaging step
# (modern manylinux cp envs no longer preinstall setuptools, which --no-build-isolation needs)
"$PY" -m pip install --quiet cmake auditwheel setuptools wheel

# build the sdk and the pybind module against this interpreter.
# notes on the python hints (manylinux quirks):
# - the cp312 env has headers but no shared libpython, so Python3_LIBRARY points at the system one
#   (the module does not link libpython, it only has to exist for the Development component)
# - PYBIND11_FINDPYTHON makes pybind11 use cmake's FindPython instead of its legacy
#   FindPythonLibsNew, which would otherwise pick the system python and break the include path
cmake -S /src -B /tmp/build \
    -DRSID_PY=ON -DRSID_TOOLS=OFF -DRSID_PIPELINE=OFF -DRSID_PREVIEW=OFF -DRSID_SAMPLES=OFF -DRSID_TESTS=OFF \
    $SECURE_CMAKE_FLAG \
    -DPYBIND11_FINDPYTHON=ON \
    -DPython3_ROOT_DIR="$PYENV" \
    -DPython3_EXECUTABLE="$PY" \
    -DPython3_INCLUDE_DIR="$PYENV/include/python3.12" \
    -DPython3_LIBRARY=/usr/lib64/libpython3.12.so.1.0
cmake --build /tmp/build --target rsid_py -j"$(nproc)"

# sdk version, same source as cmake/Version.cmake
major=$(grep -oP '^#define RSID_VER_MAJOR \K[0-9]+' /src/include/RealSenseID/Version.h)
minor=$(grep -oP '^#define RSID_VER_MINOR \K[0-9]+' /src/include/RealSenseID/Version.h)
patch=$(grep -oP '^#define RSID_VER_PATCH \K[0-9]+' /src/include/RealSenseID/Version.h)

# package the prebuilt module + librsid, then vendor librsid into the wheel (manylinux tag).
# (copy the packaging dir to a writable location - /src is mounted read-only and pip builds in-tree)
cp -r /src/packaging/python /tmp/pkg
rm -rf /tmp/pkg/build # stale setuptools build dir would leak old files into the wheel
RSID_BUILD_DIR=/tmp/build RSID_PY_VERSION="$major.$minor.$patch" RSID_PY_NAME="$PKG_NAME" \
    "$PY" -m pip wheel --no-deps --no-build-isolation -w /tmp/wheelhouse /tmp/pkg
"$PY" -m auditwheel repair /tmp/wheelhouse/*.whl -w /src/dist

# sanity check: install the repaired wheel and import the module
"$PY" -m pip install --quiet --force-reinstall --no-index --find-links /src/dist "$PKG_NAME"
"$PY" -c "import rsid_py; print('import ok:', rsid_py.__file__)"

echo "wheel ready:"
ls -lh /src/dist
