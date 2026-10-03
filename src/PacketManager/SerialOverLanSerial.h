// License: Apache 2.0. See LICENSE file in root directory.
// Copyright(c) 2020-2021 RealSense, Inc. All Rights Reserved.

#pragma once

#include "SerialConnection.h"

namespace RealSenseID
{
namespace PacketManager
{
// true if the given serial port string is a serial-over-lan address of the form "tcp://host:port"
bool IsSerialOverLanAddress(const char* port);

// Serial connection tunneled over a tcp "serial over lan" bridge.
// The config port is a "tcp://host:port" address (e.g. "tcp://192.168.1.42:12345") of a server which forwards
// everything to the real serial port it owns (see serial-over-lan-server/).
// Baudrate and other serial settings are ignored - they apply only on the remote side.
class SerialOverLanSerial : public SerialConnection
{
public:
    explicit SerialOverLanSerial(const SerialConfig& config);
    ~SerialOverLanSerial() override;

    SerialOverLanSerial(const SerialOverLanSerial&) = delete;
    SerialOverLanSerial(const SerialOverLanSerial&&) = delete;
    SerialOverLanSerial operator=(const SerialOverLanSerial&) = delete;
    SerialOverLanSerial operator=(const SerialOverLanSerial&&) = delete;

    // send all bytes and return status
    SerialStatus SendBytes(const char* buffer, size_t n_bytes) final;

    // receive all bytes and copy to the buffer
    SerialStatus RecvBytes(char* buffer, size_t n_bytes) final;

    // discard any data pending in the connection
    void Clear() final;

private:
    int _socket = -1;
};
} // namespace PacketManager
} // namespace RealSenseID
