#!/usr/bin/env bash
# Builds the rsid-py wheel for macos arm64 (cp312).
# Run on an arm64 mac with python 3.12 as the active interpreter - the GH Actions
# macos-14/15 runners with actions/setup-python provide exactly that (see
# .github/workflows/python-package.yml). Produces dist/rsid_py-<ver>-cp312-cp312-macosx_*_arm64.whl.
set -euo pipefail

PY=python3

"$PY" -m pip install --quiet delocate setuptools wheel

# build the sdk and the pybind module against this interpreter.
# deployment target 11.0 = the first arm64 macos, so the wheel runs on macos 11 and up
cmake -S . -B build \
    -DRSID_PY=ON -DRSID_TOOLS=OFF -DRSID_PIPELINE=OFF -DRSID_PREVIEW=OFF -DRSID_SAMPLES=OFF -DRSID_TESTS=OFF \
    -DPYBIND11_FINDPYTHON=ON \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0 \
    -DPython3_EXECUTABLE="$(command -v "$PY")"
cmake --build build --target rsid_py -j"$(sysctl -n hw.ncpu)"

# point the module at librsid by absolute path so delocate vendors it into the wheel
# (it rewrites the reference to its own rsid_py.dylibs/ copy afterwards)
MODULE=$(ls build/lib/rsid_py*.so)
install_name_tool -change @rpath/librsid.dylib "$PWD/build/lib/librsid.dylib" "$MODULE"
codesign --force --sign - "$MODULE" # arm64 binaries need a valid signature after modification

# sdk version, same source as cmake/Version.cmake (bsd grep has no -P)
major=$("$PY" - <<'EOF'
import re
src = open("include/RealSenseID/Version.h").read()
print(".".join(re.search(rf"#define RSID_VER_{p} ([0-9]+)", src).group(1) for p in ("MAJOR", "MINOR", "PATCH")))
EOF
)

# package the prebuilt module, then vendor librsid into the wheel (delocate follows the absolute path)
rm -rf packaging/python/build # stale setuptools build dir would leak old files into the wheel
RSID_BUILD_DIR="$PWD/build" RSID_PY_VERSION="$major" \
    "$PY" -m pip wheel --no-deps --no-build-isolation -w wheelhouse packaging/python
delocate-wheel --require-archs arm64 -w dist wheelhouse/*.whl

# sanity check: install the wheel and import the module
"$PY" -m pip install --quiet --force-reinstall --no-index --find-links dist rsid-py
"$PY" -c "import rsid_py; print('import ok:', rsid_py.__file__)"

echo "wheel ready:"
ls -lh dist
