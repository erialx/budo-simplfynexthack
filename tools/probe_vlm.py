#!/usr/bin/env python3
"""Send a minimal eight-frame request to the local NaVILA VLM server."""

import argparse
import base64
import json
import socket
from io import BytesIO

from PIL import Image


def receive_exact(sock: socket.socket, size: int) -> bytes:
    chunks = []
    received = 0
    while received < size:
        chunk = sock.recv(size - received)
        if not chunk:
            raise ConnectionError("VLM server closed the connection early")
        chunks.append(chunk)
        received += len(chunk)
    return b"".join(chunks)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=54321)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    image = Image.new("RGB", (384, 384), color=(96, 128, 160))
    encoded = BytesIO()
    image.save(encoded, format="JPEG")
    frame = base64.b64encode(encoded.getvalue()).decode("ascii")
    request = {
        "images": [frame] * 8,
        "query": "Move forward toward the hallway, then stop at the doorway.",
    }
    payload = json.dumps(request).encode("utf-8")

    with socket.create_connection((args.host, args.port), timeout=args.timeout) as sock:
        sock.settimeout(args.timeout)
        sock.sendall(len(payload).to_bytes(8, "big"))
        sock.sendall(payload)
        response_size = int.from_bytes(receive_exact(sock, 8), "big")
        response = json.loads(receive_exact(sock, response_size).decode("utf-8"))

    print(response)


if __name__ == "__main__":
    main()
