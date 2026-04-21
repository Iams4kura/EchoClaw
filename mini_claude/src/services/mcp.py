"""MCP (Model Context Protocol) client for stdio-based tool servers.

Reference: src/services/mcp/ (MCP server management)
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class MCPError(Exception):
    """MCP communication error."""
    pass


class MCPClient:
    """Manages a single MCP server process via stdio JSON-RPC 2.0."""

    def __init__(
        self,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._request_id: int = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._tools_cache: Optional[List[dict]] = None

    @property
    def is_connected(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        """Start the MCP server subprocess."""
        if self.is_connected:
            return

        try:
            import os
            env = {**os.environ}
            if self.env:
                env.update(self.env)

            cmd_parts = [self.command] + self.args
            self._proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self._reader_task = asyncio.create_task(self._read_responses())

            # Initialize with MCP protocol handshake
            await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mini_claude", "version": "0.1.0"},
            })

            # Send initialized notification
            await self._send_notification("notifications/initialized", {})

            logger.info("MCP server '%s' started (pid=%d)", self.name, self._proc.pid)
        except Exception as e:
            logger.error("Failed to start MCP server '%s': %s", self.name, e)
            raise MCPError(f"Failed to start MCP server '{self.name}': {e}")

    async def stop(self) -> None:
        """Stop the MCP server subprocess."""
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None

        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                self._proc.kill()
                await self._proc.wait()
            logger.info("MCP server '%s' stopped", self.name)

        self._proc = None
        self._pending.clear()
        self._tools_cache = None

    async def restart(self) -> None:
        """Restart the MCP server."""
        await self.stop()
        await self.start()

    async def list_tools(self) -> List[dict]:
        """Get available tools from the MCP server."""
        if self._tools_cache is not None:
            return self._tools_cache

        if not self.is_connected:
            await self.start()

        result = await self._send_request("tools/list", {})
        self._tools_cache = result.get("tools", [])
        return self._tools_cache

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Call a tool on the MCP server.

        Returns dict with 'content' and optionally 'isError'.
        """
        if not self.is_connected:
            # Auto-reconnect once
            try:
                await self.restart()
            except MCPError:
                return {"content": f"MCP server '{self.name}' is not connected", "isError": True}

        try:
            result = await self._send_request("tools/call", {
                "name": name,
                "arguments": arguments,
            })
            # Normalize content: MCP returns content as list of content blocks
            content_blocks = result.get("content", [])
            text_parts = []
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)
            content = "\n".join(text_parts) if text_parts else json.dumps(result, ensure_ascii=False)
            return {
                "content": content,
                "isError": result.get("isError", False),
            }
        except MCPError as e:
            return {"content": str(e), "isError": True}

    async def _send_request(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC request and wait for response."""
        if not self._proc or not self._proc.stdin:
            raise MCPError("Not connected")

        self._request_id += 1
        req_id = self._request_id
        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        line = json.dumps(message) + "\n"
        self._proc.stdin.write(line.encode())
        await self._proc.stdin.drain()

        try:
            result = await asyncio.wait_for(future, timeout=30)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise MCPError(f"Request timed out: {method}")

    async def _send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self._proc or not self._proc.stdin:
            return

        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        line = json.dumps(message) + "\n"
        self._proc.stdin.write(line.encode())
        await self._proc.stdin.drain()

    async def _read_responses(self) -> None:
        """Background task: read JSON-RPC responses from stdout."""
        try:
            while self._proc and self._proc.stdout:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    data = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue

                req_id = data.get("id")
                if req_id is not None and req_id in self._pending:
                    future = self._pending.pop(req_id)
                    if "error" in data:
                        err = data["error"]
                        future.set_exception(MCPError(
                            f"MCP error {err.get('code', -1)}: {err.get('message', 'Unknown')}"
                        ))
                    else:
                        future.set_result(data.get("result", {}))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("MCP reader error for '%s': %s", self.name, e)
        finally:
            # Fail any pending requests
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(MCPError("Connection closed"))
            self._pending.clear()
