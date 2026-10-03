// License: Apache 2.0. See LICENSE file in root directory.
// Copyright(c) 2020-2021 RealSense, Inc. All Rights Reserved.
#include "SerialOverLanSerial.h"
#include "CommonTypes.h"
#include "Logger.h"
#include "Timer.h"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>

#include <fcntl.h>
#include <netdb.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>

// not defined on all platforms (e.g. macos uses SO_NOSIGPIPE instead)
#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif

static const char* LOG_TAG = "SerialOverLanSerial";

namespace RealSenseID
{
namespace PacketManager
{
namespace
{
// overall deadline for establishing the tcp connection
constexpr int connect_timeout_ms = 3000;

// poll until the socket is writable, i.e. a non blocking connect completed. returns poll's rv (>0 ready, 0 timeout, -1 error)
int WaitWritable(int socket_fd, int timeout_ms)
{
    struct pollfd pfd = {socket_fd, POLLOUT, 0};
    int rv;
    do
    {
        rv = ::poll(&pfd, 1, timeout_ms);
    } while (rv < 0 && errno == EINTR);
    return rv;
}
} // namespace

bool IsSerialOverLanAddress(const char* port)
{
    return port != nullptr && ::strncmp(port, "tcp://", 6) == 0;
}

SerialOverLanSerial::~SerialOverLanSerial()
{
    try
    {
        if (_socket >= 0)
            ::close(_socket);
    }
    catch (...)
    {
    }
}

SerialOverLanSerial::SerialOverLanSerial(const SerialConfig& config)
{
    if (config.port == nullptr)
    {
        throw std::runtime_error("SerialOverLan: port is null. Expected a \"tcp://host:port\" address");
    }

    // strip the "tcp://" prefix if present
    std::string address {config.port};
    if (IsSerialOverLanAddress(address.c_str()))
    {
        address.erase(0, 6);
    }

    // parse the address - split on the last ':' so bracketed ipv6 hosts (e.g. "[::1]:1234") are also accepted
    auto colon_pos = address.rfind(':');
    if (colon_pos == std::string::npos || colon_pos == 0 || colon_pos + 1 == address.length())
    {
        throw std::runtime_error("SerialOverLan: invalid address \"" + address + "\". Expected \"host:port\"");
    }
    std::string host = address.substr(0, colon_pos);
    const std::string port_str = address.substr(colon_pos + 1);
    if (!host.empty() && host.front() == '[' && host.back() == ']')
    {
        host = host.substr(1, host.length() - 2);
    }

    // validate the port number
    char* parse_end = nullptr;
    const long port = ::strtol(port_str.c_str(), &parse_end, 10);
    if (port_str.empty() || *parse_end != '\0' || port <= 0 || port > 65535)
    {
        throw std::runtime_error("SerialOverLan: invalid port \"" + port_str + "\" in address \"" + address + "\"");
    }

    LOG_DEBUG(LOG_TAG, "Connecting to %s:%ld", host.c_str(), port);

    struct addrinfo hints;
    ::memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;

    struct addrinfo* address_list = nullptr;
    auto gai_rv = ::getaddrinfo(host.c_str(), port_str.c_str(), &hints, &address_list);
    if (gai_rv != 0)
    {
        throw std::runtime_error("SerialOverLan: failed to resolve \"" + host + "\". " + gai_strerror(gai_rv));
    }

    int last_errno = 0;
    for (auto* ai = address_list; ai != nullptr; ai = ai->ai_next)
    {
        _socket = ::socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
        if (_socket < 0)
        {
            last_errno = errno;
            continue;
        }

        // connect in non blocking mode so we can apply a timeout
        auto orig_flags = ::fcntl(_socket, F_GETFL, 0);
        if (orig_flags < 0 || ::fcntl(_socket, F_SETFL, orig_flags | O_NONBLOCK) < 0)
        {
            last_errno = errno;
            ::close(_socket);
            _socket = -1;
            continue;
        }

        auto connect_rv = ::connect(_socket, ai->ai_addr, ai->ai_addrlen);
        bool connected = connect_rv == 0;
        if (!connected && errno == EINPROGRESS && WaitWritable(_socket, connect_timeout_ms) > 0)
        {
            int so_error = 0;
            socklen_t so_error_len = sizeof(so_error);
            if (::getsockopt(_socket, SOL_SOCKET, SO_ERROR, &so_error, &so_error_len) == 0 && so_error == 0)
            {
                connected = true;
            }
            else
            {
                last_errno = (so_error != 0) ? so_error : errno;
            }
        }
        else if (!connected)
        {
            last_errno = (errno == EINPROGRESS) ? ETIMEDOUT : errno;
        }

        // restore blocking mode
        ::fcntl(_socket, F_SETFL, orig_flags);

        if (connected)
        {
            break;
        }

        // could not connect to this address - try the next one
        ::close(_socket);
        _socket = -1;
    }
    ::freeaddrinfo(address_list);

    if (_socket < 0)
    {
        throw std::runtime_error("SerialOverLan: failed to connect to \"" + address + "\". errno: " + std::to_string(last_errno));
    }

    // disable nagle - the packet protocol is request / response and sensitive to latency
    int no_delay = 1;
    ::setsockopt(_socket, IPPROTO_TCP, TCP_NODELAY, &no_delay, sizeof(no_delay));

#ifdef SO_NOSIGPIPE
    int no_sigpipe = 1;
    ::setsockopt(_socket, SOL_SOCKET, SO_NOSIGPIPE, &no_sigpipe, sizeof(no_sigpipe));
#endif // SO_NOSIGPIPE

    // discard any existing data in input buffer
    Clear();
}

SerialStatus SerialOverLanSerial::SendBytes(const char* buffer, size_t n_bytes)
{
    size_t bytes_sent = 0;
    while (n_bytes > bytes_sent)
    {
        auto* send_ptr = &buffer[bytes_sent];
        size_t n_bytes_left = n_bytes - bytes_sent;
        DEBUG_SERIAL(LOG_TAG, "[snd]", send_ptr, n_bytes_left);
        auto send_rv = ::send(_socket, send_ptr, n_bytes_left, MSG_NOSIGNAL);
        if (send_rv <= 0)
        {
            LOG_ERROR(LOG_TAG, "Error while sending %zu bytes. errno=%d, sent so far: %zu, send rv=%zu", n_bytes, errno, bytes_sent,
                      send_rv);
            return SerialStatus::SendFailed;
        }
        bytes_sent += static_cast<size_t>(send_rv);
#ifdef RSID_DEBUG_SERIAL
        LOG_DEBUG(LOG_TAG, "[snd] Sent %zu/%zu", bytes_sent, n_bytes);
#endif
    }

    return SerialStatus::Ok;
}

// receive all bytes and copy to the buffer or return error status
SerialStatus SerialOverLanSerial::RecvBytes(char* buffer, size_t n_bytes)
{
    if (n_bytes == 0)
    {
        LOG_ERROR(LOG_TAG, "Attempt to recv 0 bytes");
        return SerialStatus::RecvFailed;
    }

    // set timeout to depend on number of bytes needed
    Timer timer {std::chrono::milliseconds {200 + 4 * n_bytes}};
    size_t total_bytes_read = 0;
    while (!timer.ReachedTimeout())
    {
        // wait for data, up to the time left on the overall deadline
        auto time_left_ms = timer.TimeLeft().count();
        if (time_left_ms < 0)
        {
            break;
        }
        struct pollfd pfd = {_socket, POLLIN, 0};
        int poll_rv;
        do
        {
            poll_rv = ::poll(&pfd, 1, static_cast<int>(time_left_ms));
        } while (poll_rv < 0 && errno == EINTR);

        if (poll_rv == 0)
        {
            break; // no data before the deadline
        }
        if (poll_rv < 0)
        {
            LOG_ERROR(LOG_TAG, "[rcv] poll errno=%d error: '%s'", errno, strerror(errno));
            return SerialStatus::RecvFailed;
        }

        char* buf_ptr = buffer + total_bytes_read;
        auto recv_rv = ::recv(_socket, buf_ptr, n_bytes - total_bytes_read, 0);
        if (recv_rv > 0)
        {
            DEBUG_SERIAL(LOG_TAG, "[rcv]", buf_ptr, recv_rv);
            total_bytes_read += static_cast<size_t>(recv_rv);

            if (total_bytes_read >= n_bytes)
            {
                return SerialStatus::Ok;
            }
        }
        else if (recv_rv == 0)
        {
            LOG_ERROR(LOG_TAG, "[rcv] connection closed by the remote side");
            return SerialStatus::RecvFailed;
        }
        else if (errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK)
        {
            LOG_ERROR(LOG_TAG, "[rcv] rv=%ld errno=%d error: '%s'", recv_rv, errno, strerror(errno));
            return SerialStatus::RecvFailed;
        }
    }

    // reached here on timout
    if (n_bytes != 1)
    {
        LOG_DEBUG(LOG_TAG, "Timeout recv %zu bytes. Got only %zu bytes", n_bytes, total_bytes_read);
    }

    return SerialStatus::RecvTimeout;
}

// tcp has no flush - drain whatever is pending in the receive buffer (best effort)
void SerialOverLanSerial::Clear()
{
    if (_socket < 0)
    {
        return;
    }
    char discard_buf[1024];
    while (true)
    {
        struct pollfd pfd = {_socket, POLLIN, 0};
        int poll_rv;
        do
        {
            poll_rv = ::poll(&pfd, 1, 0);
        } while (poll_rv < 0 && errno == EINTR);
        if (poll_rv <= 0)
        {
            break; // nothing (more) to read
        }
        auto recv_rv = ::recv(_socket, discard_buf, sizeof(discard_buf), 0);
        if (recv_rv <= 0)
        {
            break;
        }
    }
}
} // namespace PacketManager
} // namespace RealSenseID
