# Python wheel packaging (rsid-py)

Builds the SDK's Python wrapper (`rsid_py`, see `wrappers/python/`) into pip-installable wheels
for **linux arm64** and **macos arm64** (CPython 3.12):

| Platform | Script | Wheel tag |
|---|---|---|
| linux arm64 | `build-wheel.sh` | `cp312-cp312-manylinux_2_27_aarch64` (built in `manylinux_2_28_aarch64`; runs on Debian 11+, Ubuntu 20.04+, Pi OS bullseye/bookworm) |
| macos arm64 | `build-wheel-macos.sh` | `cp312-cp312-macosx_11_0_arm64` (runs on macOS 11+) |

The linux build happens inside the official `manylinux_2_28_aarch64` container (docker); the macos
build runs natively (it needs Xcode command line tools + `python3`). Both scripts build the SDK and
wrapper with cmake against the target interpreter, collect the prebuilt `rsid_py` module with
`setup.py`, and vendor `librsid` into the wheel (`auditwheel` on linux, `delocate` on macos). Each
script ends with an install + import sanity check.

## CI

`.github/workflows/python-package.yml`:

- **workflow_dispatch** (manual run) — builds both wheels, uploads them as workflow artifacts
- **tag push `v*`** — same, plus attaches the wheels to the GitHub release of that tag

Install on the arm64 device host / mac:

```bash
pip install https://github.com/<you>/RealSenseID/releases/download/<tag>/<wheel-file>.whl
```

Then (device discovery does not work over the network — pick the `DeviceType` yourself):

```python
import rsid_py
fa = rsid_py.FaceAuthenticator(rsid_py.DeviceType.F45x, "tcp://192.168.1.42:12345")
```

## Local build

Linux arm64 (any arm64 machine with docker), from the repo root:

```bash
mkdir -p dist
docker run --rm -v "$(pwd)":/src:ro -v "$(pwd)/dist":/src/dist \
    quay.io/pypa/manylinux_2_28_aarch64 bash /src/packaging/python/build-wheel.sh
```

macOS arm64 (Xcode CLT installed), with python 3.12 active:

```bash
bash packaging/python/build-wheel-macos.sh
```

The wheel version comes from `include/RealSenseID/Version.h` (what cmake/Version.cmake reads too).
The PyPI-style project name is `rsid-py`; the importable module stays `rsid_py`.

Note: the wrapper is built without `RSID_SECURE` (secure mode is not supported for the Python
wrapper, per `wrappers/python/CMakeLists.txt`) and without the preview/pipeline extras. Building on
macOS is enabled by small changes in `cmake/OS.cmake`, the serial `#ifdef` guards and
`src/PacketManager/CMakeLists.txt` — regular USB serial (`LinuxSerial`) and `tcp://` serial both
work there.
