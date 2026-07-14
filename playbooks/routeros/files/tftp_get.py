#!/usr/bin/env python3
"""Minimal RFC 1350 TFTP read client (octet mode, 512-byte blocks).

Replaces ``curl tftp://...`` in the netboot verify stage: the igou-awx-ee
execution environment and this repo's devcontainer both ship curl-minimal,
which is built without TFTP protocol support (curl exits 1 instantly with
CURLE_UNSUPPORTED_PROTOCOL), so the e2e TFTP check could never pass from
AAP. stdlib only; no options negotiation (blksize etc.) -- servers fall
back to plain 512-byte blocks per the RFC.

Usage: tftp_get.py <host>[/<path-prefix>] <filename> <dest>

The optional /<path-prefix> on the host argument mirrors how the verify
tasks derive the server from vars like netboot_chainload_host
("host/prefix"): the prefix is prepended to the requested filename.
"""

import socket
import struct
import sys

RRQ_RETRIES = 3
TIMEOUT_S = 5.0
BLOCK_SIZE = 512


def die(msg):
    print("tftp_get: " + msg, file=sys.stderr)
    sys.exit(1)


def main():
    if len(sys.argv) != 4:
        die("usage: tftp_get.py <host>[/<prefix>] <filename> <dest>")
    host, _, prefix = sys.argv[1].partition("/")
    filename = prefix + "/" + sys.argv[2] if prefix else sys.argv[2]
    dest = sys.argv[3]

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(TIMEOUT_S)
    rrq = struct.pack("!H", 1) + filename.encode() + b"\0octet\0"

    # UDP can drop the first packet; retry the initial RRQ a few times.
    data = addr = None
    for _ in range(RRQ_RETRIES):
        sock.sendto(rrq, (host, 69))
        try:
            data, addr = sock.recvfrom(4 + BLOCK_SIZE)
            break
        except socket.timeout:
            continue
    if data is None:
        die("no response from %s:69 for %s after %d RRQ attempts"
            % (host, filename, RRQ_RETRIES))

    expected = 1
    with open(dest, "wb") as out:
        while True:
            opcode, block = struct.unpack("!HH", data[:4])
            if opcode == 5:  # ERROR
                detail = data[4:].rstrip(b"\0").decode(errors="replace")
                die("server error %d fetching %s: %s" % (block, filename, detail))
            if opcode != 3:  # not DATA
                die("unexpected opcode %d fetching %s" % (opcode, filename))
            if block == expected:
                out.write(data[4:])
                expected += 1
            # ACK every DATA (including duplicates, whose ACK may have
            # been lost); transfer ends on a short final block.
            sock.sendto(struct.pack("!HH", 4, block), addr)
            if block == expected - 1 and len(data) - 4 < BLOCK_SIZE:
                break
            try:
                data, addr = sock.recvfrom(4 + BLOCK_SIZE)
            except socket.timeout:
                die("timed out mid-transfer fetching %s (waiting for block %d)"
                    % (filename, expected))


if __name__ == "__main__":
    main()
