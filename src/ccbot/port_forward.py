"""Port forwarding helpers for exposing local dev services.

Starts public tunnels for local ports using available CLI tools
(`ngrok` preferred, `cloudflared` fallback, `ssh`/localhost.run as last resort).
"""

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_NGROK_URL_RE = re.compile(
    r"https://[A-Za-z0-9.-]+\.(?:ngrok-free\.app|ngrok\.app|ngrok\.io)"
)
_CF_URL_RE = re.compile(r"https://[A-Za-z0-9-]+\.trycloudflare\.com")
_LOCALHOST_RUN_URL_RE = re.compile(
    r"https://(?!admin(?:\.|$))[A-Za-z0-9-]{8,}\.(?:localhost\.run|lhr\.life)"
)


@dataclass
class PortTunnel:
    port: int
    public_url: str
    provider: str
    process: asyncio.subprocess.Process


class PortForwardManager:
    """Manage public tunnels for local ports."""

    def __init__(self, ports: list[int]) -> None:
        self.ports = ports
        self.tunnels: list[PortTunnel] = []

    async def start(self) -> list[PortTunnel]:
        for port in self.ports:
            tunnel = await self._start_port(port)
            self.tunnels.append(tunnel)
            logger.info(
                "Forward tunnel ready: provider=%s port=%d url=%s",
                tunnel.provider,
                tunnel.port,
                tunnel.public_url,
            )
        return list(self.tunnels)

    async def stop(self) -> None:
        for tunnel in self.tunnels:
            proc = tunnel.process
            if proc.returncode is not None:
                continue
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        self.tunnels.clear()

    async def _start_port(self, port: int) -> PortTunnel:
        providers: list[str] = []
        if shutil.which("ngrok"):
            providers.append("ngrok")
        if shutil.which("cloudflared"):
            providers.append("cloudflared")
        if shutil.which("ssh"):
            providers.append("localhost.run")
        if not providers:
            raise RuntimeError(
                "No tunnel provider found. Install ngrok/cloudflared or ensure ssh is available."
            )

        errors: list[str] = []
        for provider in providers:
            try:
                if provider == "ngrok":
                    return await self._start_ngrok(port)
                if provider == "cloudflared":
                    return await self._start_cloudflared(port)
                return await self._start_localhost_run(port)
            except RuntimeError as e:
                errors.append(f"{provider}: {e}")
                logger.warning("Tunnel provider failed for port %d: %s", port, e)
                continue

        raise RuntimeError(
            f"Failed to create tunnel for port {port}. " + "; ".join(errors)
        )

    async def _start_ngrok(self, port: int) -> PortTunnel:
        cmd = [
            "ngrok",
            "http",
            f"http://127.0.0.1:{port}",
            "--log=stdout",
            "--log-format=json",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        url = await self._wait_for_url(
            proc=proc,
            provider="ngrok",
            port=port,
            regex=_NGROK_URL_RE,
            timeout_seconds=30.0,
        )
        return PortTunnel(port=port, public_url=url, provider="ngrok", process=proc)

    async def _start_cloudflared(self, port: int) -> PortTunnel:
        cmd = [
            "cloudflared",
            "tunnel",
            "--url",
            f"http://127.0.0.1:{port}",
            "--no-autoupdate",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        url = await self._wait_for_url(
            proc=proc,
            provider="cloudflared",
            port=port,
            regex=_CF_URL_RE,
            timeout_seconds=40.0,
        )
        return PortTunnel(
            port=port, public_url=url, provider="cloudflared", process=proc
        )

    async def _start_localhost_run(self, port: int) -> PortTunnel:
        cmd = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-R",
            f"80:127.0.0.1:{port}",
            "nokey@localhost.run",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        url = await self._wait_for_url(
            proc=proc,
            provider="localhost.run",
            port=port,
            regex=_LOCALHOST_RUN_URL_RE,
            timeout_seconds=45.0,
        )
        return PortTunnel(
            port=port,
            public_url=url,
            provider="localhost.run",
            process=proc,
        )

    async def _wait_for_url(
        self,
        *,
        proc: asyncio.subprocess.Process,
        provider: str,
        port: int,
        regex: re.Pattern[str],
        timeout_seconds: float,
    ) -> str:
        if proc.stdout is None:
            raise RuntimeError(f"{provider} stdout is unavailable")

        deadline = asyncio.get_event_loop().time() + timeout_seconds
        tail: list[str] = []

        while True:
            if proc.returncode is not None:
                break
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if not line:
                continue
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                tail.append(text)
                if len(tail) > 20:
                    tail = tail[-20:]
            m = regex.search(text)
            if m:
                return m.group(0)

        # Cleanup failed process
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

        reason = (
            f"process exited with code {proc.returncode}"
            if proc.returncode is not None
            else "startup timeout"
        )
        sample = " | ".join(tail[-5:]) if tail else "no output"
        raise RuntimeError(
            f"{provider} failed for port {port}: {reason}; output: {sample}"
        )
