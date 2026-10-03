#!/usr/bin/env python3
# uv script metadata - lets `uv run server.py` resolve the dependencies automatically (see SETUP.md)
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "pyserial>=3.5",
# ]
# ///
# Simple serial-over-lan server: bridges a local serial port to a raw tcp connection.
# Use it with the RealSense ID SDK by passing a "host:port" address as the serial port,
# e.g. FaceAuthenticator::Connect({"192.168.1.42:12345"}) - see SerialOverLanSerial in the sdk.
#
# The protocol is plain tcp passthrough - no framing, no baudrate negotiation.
# A single tcp client is served at a time; connections from additional clients are closed immediately.
#
# Configuration (environment variables):
#   SERIAL_PATH     serial device to bridge, e.g. /dev/ttyUSB0          (required)
#   SERIAL_BAUDRATE baudrate of the serial port                          (default: 115200)
#   LISTEN_PORT     tcp port to listen on                                (default: 12345)
#   LISTEN_HOST     address to bind to                                   (default: 0.0.0.0)

import os
import socket
import sys
import threading

import serial

CHUNK_SIZE = 4096
READ_TIMEOUT = 0.05  # serial read timeout - keeps the serial->tcp loop responsive to shutdown


def log(msg):
    print(f"[server] {msg}", flush=True)


def die(msg):
    # called from worker threads too - sys.exit() would only exit the calling thread
    print(f"[server] error: {msg}", file=sys.stderr, flush=True)
    os._exit(1)


def pump_tcp_to_serial(conn, ser, stop):
    """socket -> serial"""
    while not stop.is_set():
        try:
            data = conn.recv(CHUNK_SIZE)
        except OSError:
            break  # client disconnected
        if not data:
            break
        try:
            ser.write(data)
        except OSError as e:
            die(f"serial port write failed: {e}")
    stop.set()


def pump_serial_to_tcp(conn, ser, stop):
    """serial -> socket"""
    while not stop.is_set():
        try:
            data = ser.read(CHUNK_SIZE)
        except OSError as e:
            die(f"serial port read failed: {e}")
        if not data:
            continue
        try:
            conn.sendall(data)
        except OSError:
            break  # client disconnected
    stop.set()


def serve_client(conn, addr, ser):
    log(f"client {addr[0]}:{addr[1]} connected")
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    # drop any stale data buffered from the device, like the sdk does when opening the port
    ser.reset_input_buffer()

    stop = threading.Event()
    pumps = [
        threading.Thread(target=pump_tcp_to_serial, args=(conn, ser, stop), daemon=True),
        threading.Thread(target=pump_serial_to_tcp, args=(conn, ser, stop), daemon=True),
    ]
    for pump in pumps:
        pump.start()

    # block until either pump ends (client disconnected / error), then tear the connection down
    stop.wait()
    try:
        conn.shutdown(socket.SHUT_RDWR)  # unblock a recv still waiting on the socket
    except OSError:
        pass
    for pump in pumps:
        pump.join(timeout=2.0)
    conn.close()
    log(f"client {addr[0]}:{addr[1]} disconnected")


def main():
    serial_path = os.environ.get("SERIAL_PATH")
    if not serial_path:
        print(f"usage: SERIAL_PATH=/dev/ttyUSB0 [SERIAL_BAUDRATE=115200] [LISTEN_PORT=12345] [LISTEN_HOST=0.0.0.0] {sys.argv[0]}",
              file=sys.stderr)
        sys.exit(2)
    try:
        baudrate = int(os.environ.get("SERIAL_BAUDRATE", "115200"))
        listen_port = int(os.environ.get("LISTEN_PORT", "12345"))
    except ValueError as e:
        die(f"invalid numeric configuration value: {e}")
    listen_host = os.environ.get("LISTEN_HOST", "0.0.0.0")

    try:
        ser = serial.Serial(serial_path, baudrate, timeout=READ_TIMEOUT)
    except (ValueError, serial.SerialException, OSError) as e:
        die(f"could not open serial port {serial_path}: {e}")

    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((listen_host, listen_port))
        listener.listen(1)
    except OSError as e:
        die(f"could not listen on {listen_host}:{listen_port}: {e}")

    log(f"bridging {serial_path} @ {baudrate} <-> tcp {listen_host}:{listen_port} (single client)")

    client_thread = None
    try:
        while True:
            conn, addr = listener.accept()
            if client_thread is not None and client_thread.is_alive():
                log(f"rejecting extra connection from {addr[0]}:{addr[1]} - a client is already connected")
                conn.close()
                continue
            client_thread = threading.Thread(target=serve_client, args=(conn, addr, ser), daemon=True)
            client_thread.start()
    except KeyboardInterrupt:
        log("shutting down")
    finally:
        listener.close()
        ser.close()


if __name__ == "__main__":
    main()
