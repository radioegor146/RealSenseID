# serial-over-lan-server

Bridges a local serial port to a raw TCP connection so the RealSense ID SDK can talk to a device
attached to another machine on the network. The protocol is plain TCP passthrough — no framing and
no baudrate negotiation (the server owns the serial port settings).

Pairs with `SerialOverLanSerial` in the SDK (`src/PacketManager/SerialOverLanSerial.*`): any serial
port string of the form `tcp://host:port` is routed over TCP instead of opening a local tty.

To run it as a systemd service on Debian-based distros (with uv managing the dependencies), see
[SETUP.md](SETUP.md).

## Requirements

- [uv](https://docs.astral.sh/uv/) — easiest: `uv run server.py` resolves Python and `pyserial` automatically (the dependency is declared inline in the script)
- or plain Python 3 + [pyserial](https://pypi.org/project/pyserial/): `pip install pyserial`

## Configuration

Environment variables, no command line arguments:

| Variable         | Default  | Description                                     |
|------------------|----------|-------------------------------------------------|
| `SERIAL_PATH`    | required | Serial device to bridge, e.g. `/dev/ttyUSB0`    |
| `SERIAL_BAUDRATE`| `115200` | Baudrate of the serial port                     |
| `LISTEN_PORT`    | `12345`  | TCP port to listen on                           |
| `LISTEN_HOST`    | `0.0.0.0`| Address to bind to                              |

## Run

On the machine the device is plugged into:

```bash
SERIAL_PATH=/dev/ttyACM0 SERIAL_BAUDRATE=115200 LISTEN_PORT=12345 python3 server.py
```

Only **one** TCP client is served at a time — connections from additional clients are closed
immediately. After the client disconnects, a new one can connect.

## SDK usage

Pass the server address (with the `tcp://` prefix) as the serial port. Note that device discovery
does not work over the network — construct the authenticator/controller with the correct `DeviceType`
(`F45x` for F450/F455, `F50x` for F460/F500) instead of relying on `DiscoverDevices()`:

```cpp
RealSenseID::FaceAuthenticator authenticator(RealSenseID::DeviceType::F45x);
auto status = authenticator.Connect({"tcp://192.168.1.42:12345"});
```

Works the same way for `DeviceController::Connect` and the firmware updater's `Settings::serial_config`.
