"""Packages the prebuilt rsid_py module (and librsid.so) into a wheel.

This does not compile anything - the CMake build in build-wheel.sh produces the binaries
and RSID_BUILD_DIR points at them. Run via build-wheel.sh inside a manylinux container,
not standalone.
"""
import glob
import os
import shutil
import sys

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext


class CopyPrebuilt(build_ext):
    """Copy the cmake-built artifacts into the wheel instead of compiling anything."""

    def build_extension(self, ext):
        build_dir = os.environ["RSID_BUILD_DIR"]

        # the pybind11 module, built against this interpreter by cmake
        modules = sorted(glob.glob(os.path.join(build_dir, "lib", f"{ext.name}*.so")))
        if not modules:
            raise RuntimeError(f"no {ext.name} module found under {build_dir}/lib - run cmake first (build-wheel.sh)")
        dest = self.get_ext_fullpath(ext.name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy(modules[0], dest)

        # the shared sdk library the module depends on:
        # - linux: copy it next to the module, auditwheel vendors it during repair
        # - macos: leave it out, delocate vendors it from the build dir (--dylibs)
        if sys.platform != "darwin":
            sdk_lib = next(
                (p for pat in ("librsid.so", "librsid.dylib") for p in glob.glob(os.path.join(build_dir, "lib", pat))), None)
            if sdk_lib is None:
                raise RuntimeError(f"librsid not found under {build_dir}/lib - run cmake first (build-wheel*.sh)")
            os.makedirs(self.build_lib, exist_ok=True)
            shutil.copy(sdk_lib, self.build_lib)


setup(
    name=os.environ.get("RSID_PY_NAME", "rsid-py"),
    version=os.environ.get("RSID_PY_VERSION", "0.0.0"),
    description="Python wrapper for the RealSense ID SDK",
    ext_modules=[Extension("rsid_py", sources=[])],
    cmdclass={"build_ext": CopyPrebuilt},
)
