#!/usr/bin/env python3
"""
QGIS MCP Client - Simple client to connect to the QGIS MCP server.

Uses length-prefixed framing: each message is preceded by a 4-byte
big-endian unsigned int indicating the JSON payload size in bytes.
"""

import asyncio
import json
import socket

from src.setting.config import (
    HEADER_STRUCT,
    HOST,
    PORT,
    TIMEOUT_DEFAULT,
)
from src.setting.logger import get_logger

logger = get_logger("QgisMCPClient")


class QgisMCPClient:
    def __init__(self, host=HOST, port=PORT):
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None

    async def connect(self):
        try:
            self.reader, self.writer = await asyncio.open_connection(
                self.host, self.port
            )
            # Disable Nagle's algorithm for low latency (TCP_NODELAY)
            sock = self.writer.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return True
        except Exception:
            logger.exception("Error connecting to server")
            return False

    async def disconnect(self):
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except (ConnectionError, EOFError):
                pass
            except Exception as e:
                return {"status": "error", "message": str(e)}

            self.writer = None
            self.reader = None

    async def send_command(
        self, command_type: str, params: dict | None = None, timeout=TIMEOUT_DEFAULT
    ):
        if not self.writer or not self.reader:
            raise ConnectionError("Not connected to server")

        command = {"type": command_type, "params": params or {}}

        try:
            data = json.dumps(command).encode("utf-8")
            header = HEADER_STRUCT.pack(len(data))

            # Send payload
            self.writer.write(header + data)
            await self.writer.drain()

            # Wait for response with timeout
            async with asyncio.timeout(timeout):
                resp_header = await self.reader.readexactly(4)
                resp_len = HEADER_STRUCT.unpack(resp_header)[0]
                resp_data = await self.reader.readexactly(resp_len)

            return json.loads(resp_data.decode("utf-8"))

        except TimeoutError:
            logger.warning(f"Socket operation timed out after {timeout}s")
            return {"status": "error", "message": "Connection timed out"}
        except asyncio.IncompleteReadError:
            raise ConnectionError("Connection closed by server")
        except (
            BrokenPipeError,
            ConnectionResetError,
            ConnectionAbortedError,
            ConnectionError,
        ):
            raise  # Let callers handle reconnection LeoDev2p
        except Exception as e:
            logger.exception("Error sending command")
            return {"status": "error", "message": str(e)}
