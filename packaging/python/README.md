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
The importable module stays `rsid_py`.

## Secure variant (`rsid-py-secure`)

Setting `RSID_PY_SECURE=1` when running a build script builds the secure sdk flavor instead:
package name `rsid-py-secure` (same `rsid_py` module — don't install both flavors into one
environment). Secure is F45x-only and speaks the secure protocol (regular operations need a paired
/ secure-SKU device). Adds `pair()`, `unpair()` and a subclassable `SignatureCallback`.

A complete runnable walkthrough lives in [`secure_pair_example.py`](secure_pair_example.py)
(`keygen` / `pair` / `demo` subcommands — requires `pip install cryptography`). Both keys are
stored as PEM; the device key is a standard SubjectPublicKeyInfo public key
(inspect with `openssl ec -pubin -in device_pubkey.pem -text`).

### The secure flow

1. **Generate a host ECDSA P-256 keypair once and keep it forever** (losing the private key after
   pairing locks you out of the device until you unpair with it):

   ```python
   from cryptography.hazmat.primitives.asymmetric import ec
   from cryptography.hazmat.primitives import serialization

   key = ec.generate_private_key(ec.SECP256R1())
   open("host_key.pem", "wb").write(key.private_bytes(  # store safely
       serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
   ```

2. **Pair** — sends your public key (64-byte `X||Y`) and its signature (64-byte raw `r||s`,
   SHA-256+P-256) to the device, which stores it and returns its own public key. **Save the device
   key** — it is how you verify the device on every later session. On very first pairing the device
   accepts any signature (it has no key yet), but sign properly anyway:

   ```python
   import rsid_py
   from cryptography.hazmat.primitives import hashes
   from cryptography.hazmat.primitives.asymmetric import ec, utils

   key = serialization.load_pem_private_key(open("host_key.pem", "rb").read(), None)
   n = key.public_key().public_numbers()
   host_pubkey = n.x.to_bytes(32, "big") + n.y.to_bytes(32, "big")
   r, s = utils.decode_dss_signature(key.sign(host_pubkey, ec.ECDSA(hashes.SHA256())))
   host_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")

   fa = rsid_py.FaceAuthenticator(rsid_py.DeviceType.F45x, "tcp://192.168.1.42:12345")
   device_pubkey = fa.pair(host_pubkey, host_sig)   # 64 bytes - store it (e.g. in a file)
   ```

3. **Operate with a real `SignatureCallback`** — every session start does an ECDH exchange signed by
   you and verified against the device key, so pass a callback subclass that signs with your
   private key and verifies with the stored device key (recipe mirrors
   `samples/cpp/secure_mode_helper.cc` — SHA-256 digest, raw 64-byte `r||s` signatures):

   ```python
   class MySigCb(rsid_py.SignatureCallback):
       def __init__(self, key, device_pubkey):
           super().__init__()
           self.key, self.device_pubkey = key, device_pubkey

       def sign(self, buffer):
           r, s = utils.decode_dss_signature(self.key.sign(buffer, ec.ECDSA(hashes.SHA256())))
           return r.to_bytes(32, "big") + s.to_bytes(32, "big")

       def verify(self, buffer, signature):
           r = int.from_bytes(signature[:32], "big"); s = int.from_bytes(signature[32:], "big")
           pub = ec.EllipticCurvePublicNumbers(
               int.from_bytes(self.device_pubkey[:32], "big"),
               int.from_bytes(self.device_pubkey[32:], "big"), ec.SECP256R1()).public_key()
           try:
               pub.verify(utils.encode_dss_signature(r, s), buffer, ec.ECDSA(hashes.SHA256()))
               return True
           except InvalidSignature:
               return False

   fa = rsid_py.FaceAuthenticator(MySigCb(key, device_pubkey), rsid_py.DeviceType.F45x,
                                  "tcp://192.168.1.42:12345")
   ```

4. **`unpair()`** reverts the device to non-secured state — it signs the all-`0xff` default public
   key with your *current* private key, so it needs the same key you paired with.

The no-callback constructors use a placeholder `SignatureCallback` internally: `pair()`/`unpair()`
input plumbing works, but session operations fail — use the callback ctor for real usage.
