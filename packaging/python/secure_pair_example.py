#!/usr/bin/env python3
"""Secure-mode example for the rsid-py-secure wheel (install the secure flavor first!).

Implements the full host-side secure flow, mirroring samples/cpp/secure_mode_helper.cc:

    # 1. generate your host keypair once (keep the private key forever!)
    python secure_pair_example.py keygen host_key.pem

    # 2. pair: sends your public key to the device, saves the device public key (PEM)
    python secure_pair_example.py pair host_key.pem device_pubkey.pem tcp://192.168.1.42:12345

    # 3. operate securely (any FaceAuthenticator op works through the callback)
    python secure_pair_example.py demo host_key.pem device_pubkey.pem tcp://192.168.1.42:12345

Both keys are stored as PEM (the device key as a standard SubjectPublicKeyInfo public key -
inspect it with `openssl ec -pubin -in device_pubkey.pem -text`).

Wire format notes (from the sdk source): public keys are 64 bytes X||Y (no 0x04 prefix),
signatures are 64 bytes of raw r||s (not DER), ECDSA P-256 over a SHA-256 digest.

The device public key is PUBLIC data - it does not need secrecy, only integrity:
an attacker who can replace your stored copy could impersonate the device. Store it somewhere
tamper-proof-ish (a file you control, or pin its sha256 fingerprint shown on first save).
"""

import hashlib
import os
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

import rsid_py


# ----------------------------------------------------------------------------- keys

def load_host_key(path):
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def load_device_pubkey(path):
    """Load the device public key from a PEM file (as written by the pair command)."""
    with open(path, "rb") as f:
        key = serialization.load_pem_public_key(f.read())
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError(f"{path}: expected a P-256 (secp256r1) public key")
    return key


def save_device_pubkey(key, path):
    with open(path, "wb") as f:
        f.write(key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))


def host_pubkey_bytes(key) -> bytes:
    """Public key as the sdk expects it: 64-byte X||Y. Accepts a private or public P-256 key."""
    public = key.public_key() if hasattr(key, "private_bytes") else key
    n = public.public_numbers()
    return n.x.to_bytes(32, "big") + n.y.to_bytes(32, "big")


def pubkey_from_bytes(xy: bytes):
    return ec.EllipticCurvePublicNumbers(
        int.from_bytes(xy[:32], "big"), int.from_bytes(xy[32:], "big"), ec.SECP256R1()
    ).public_key()


def raw_sign(key, data: bytes) -> bytes:
    """Sign data -> 64-byte raw r||s (the sdk's format, converted from DER)."""
    r, s = utils.decode_dss_signature(key.sign(data, ec.ECDSA(hashes.SHA256())))
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


# ------------------------------------------------------------------- the callback

class MySigCb(rsid_py.SignatureCallback):
    """Signs with the host private key, verifies with the paired device public key.

    The device key can be an EllipticCurvePublicKey (e.g. from load_device_pubkey) or the
    raw 64-byte X||Y form returned by pair().
    """

    def __init__(self, host_key, device_pubkey):
        super().__init__()
        self._host_key = host_key
        self._device_pub = pubkey_from_bytes(device_pubkey) if isinstance(device_pubkey, (bytes, bytearray)) \
            else device_pubkey

    def sign(self, buffer: bytes) -> bytes:
        return raw_sign(self._host_key, buffer)

    def verify(self, buffer: bytes, signature: bytes) -> bool:
        r = int.from_bytes(signature[:32], "big")
        s = int.from_bytes(signature[32:], "big")
        try:
            self._device_pub.verify(utils.encode_dss_signature(r, s), buffer, ec.ECDSA(hashes.SHA256()))
            return True
        except InvalidSignature:
            return False


# -------------------------------------------------------------------- cli commands

def cmd_keygen(host_key_path):
    key = ec.generate_private_key(ec.SECP256R1())
    with open(host_key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    os.chmod(host_key_path, 0o600)  # this one IS secret
    print(f"host key written to {host_key_path} (guard it - losing it after pairing locks the device)")
    print(f"host public key (hex): {host_pubkey_bytes(key).hex()}")


def cmd_pair(host_key_path, device_key_path, address):
    key = load_host_key(host_key_path)
    host_pubkey = host_pubkey_bytes(key)
    host_sig = raw_sign(key, host_pubkey)

    fa = rsid_py.FaceAuthenticator(rsid_py.DeviceType.F45x, address)
    device_pubkey = fa.pair(host_pubkey, host_sig)  # 64-byte X||Y
    fa.disconnect()

    save_device_pubkey(pubkey_from_bytes(device_pubkey), device_key_path)
    print(f"device public key saved to {device_key_path}")
    print(f"  X||Y hex:     {device_pubkey.hex()}")
    print(f"  fingerprint:  sha256:{hashlib.sha256(device_pubkey).hexdigest()}")
    # tip: name the file per device, e.g. device_pubkey_<serial>.pem where the serial comes from
    # rsid_py.DeviceController(...).query_serial_number()


def cmd_demo(host_key_path, device_key_path, address):
    key = load_host_key(host_key_path)
    device_key = load_device_pubkey(device_key_path)

    fa = rsid_py.FaceAuthenticator(MySigCb(key, device_key), rsid_py.DeviceType.F45x, address)
    n_users = fa.query_number_of_users()  # any op works - packets are encrypted + hmac'd
    print(f"secure session ok, device reports {n_users} enrolled user(s)")
    fa.disconnect()


def main():
    usage = "usage: secure_pair_example.py {keygen <host_key.pem> | pair <host_key.pem> <device_pubkey.pem> <address> | demo <host_key.pem> <device_pubkey.pem> <address>}"
    if len(sys.argv) < 2:
        sys.exit(usage)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "keygen" and len(args) == 1:
        cmd_keygen(args[0])
    elif cmd == "pair" and len(args) == 3:
        cmd_pair(*args)
    elif cmd == "demo" and len(args) == 3:
        cmd_demo(*args)
    else:
        sys.exit(usage)


if __name__ == "__main__":
    main()
