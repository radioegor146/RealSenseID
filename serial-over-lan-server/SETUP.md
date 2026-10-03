# Setup on Debian-based distros (systemd + uv)

Run the whole bridge as a systemd service, with [uv](https://docs.astral.sh/uv/) handling Python and
the `pyserial` dependency — no system Python packages needed. `server.py` declares its dependency
inline ([PEP 723](https://peps.python.org/pep-0723/)), so `uv run server.py` sets everything up
automatically.

All commands below are run as root (`sudo -i` or prefix with `sudo`), from this directory.

## 1. Install uv

Debian doesn't package uv, so use the official installer. `UV_UNMANAGED_INSTALL` puts the binary at a
stable system path without touching any shell profiles:

```bash
curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="/usr/local/bin" sh
```

## 2. Install the server

```bash
mkdir -p /opt/serial-over-lan-server
cp server.py /opt/serial-over-lan-server/
```

## 3. Configure

Install the environment file and point `SERIAL_PATH` at your device (find it with `dmesg | tail`
after plugging in, or `ls /dev/ttyACM* /dev/ttyUSB*`):

```bash
cp serial-over-lan-server.env /etc/default/serial-over-lan-server
nano /etc/default/serial-over-lan-server
```

## 4. Install and start the service

```bash
cp serial-over-lan-server.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now serial-over-lan-server
```

> The first start needs internet access: `uv run` caches a Python interpreter (if the system has
> none) and the `pyserial` wheel. After that, restarts work offline.

## 5. Check it

```bash
systemctl status serial-over-lan-server
journalctl -u serial-over-lan-server -f
```

If the port is firewalled, open the TCP port: `ufw allow <LISTEN_PORT>/tcp`
(or `firewall-cmd --add-port=<LISTEN_PORT>/tcp --permanent && firewall-cmd --reload`).

## Notes

- **Device name changes**: if `/dev/ttyACM0` re-enumerates as a different number, use a stable
  symlink provided by udev (`ls -l /dev/serial/by-id/`) as `SERIAL_PATH`.
- **Unprivileged run**: by default the service runs as root. See the commented `User=` lines in the
  unit file to run it as a dedicated user in the `dialout` group instead.
- **Manual run** (no systemd): `SERIAL_PATH=/dev/ttyACM0 uv run server.py` from this directory.
- **Plain python3 also works** (uv is just the easy path): `apt install python3-serial` and change
  `ExecStart` to `/usr/bin/python3 /opt/serial-over-lan-server/server.py`.
