from __future__ import annotations

import argparse
import grp
import json
import logging
import os
import pwd
import socket
import socketserver
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pamela

logger = logging.getLogger(__name__)
MAX_MESSAGE = 4096
SO_PEERCRED = getattr(socket, "SO_PEERCRED", 17)


@dataclass(frozen=True)
class AuthConfig:
    socket_path: Path
    web_uid: int
    web_gid: int
    pam_service: str
    minimum_uid: int

    @classmethod
    def from_environment(cls) -> AuthConfig:
        web_user = os.getenv("GPU_WEB_USER", "gpu-booking")
        web_group = os.getenv("GPU_WEB_GROUP", "gpu-booking")
        user_entry = pwd.getpwnam(web_user)
        group_entry = grp.getgrnam(web_group)
        return cls(
            socket_path=Path(os.getenv("GPU_AUTH_SOCKET", "/run/gpu-booking/auth.sock")),
            web_uid=user_entry.pw_uid,
            web_gid=group_entry.gr_gid,
            pam_service=os.getenv("PAM_SERVICE", "gpu-booking"),
            minimum_uid=int(os.getenv("MIN_INTERACTIVE_UID", "1000")),
        )


def eligible_identity(username: str, minimum_uid: int) -> pwd.struct_passwd | None:
    try:
        identity = pwd.getpwnam(username)
    except KeyError:
        return None
    if identity.pw_uid == 0 or identity.pw_uid < minimum_uid:
        return None
    if identity.pw_shell in {"", "/bin/false", "/sbin/nologin", "/usr/sbin/nologin"}:
        return None
    return identity


def authenticate_payload(payload: Any, config: AuthConfig) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {"ok": False}
    username = payload.get("username")
    password = payload.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        return {"ok": False}
    if not username or len(username) > 150 or "\x00" in username or len(password) > 2048:
        return {"ok": False}
    identity = eligible_identity(username, config.minimum_uid)
    if identity is None:
        return {"ok": False}
    try:
        pamela.authenticate(username, password, service=config.pam_service)
    except Exception:
        return {"ok": False}
    return {"ok": True, "uid": identity.pw_uid}


class AuthRequestHandler(socketserver.BaseRequestHandler):
    server: AuthServer

    def handle(self) -> None:
        self.request.settimeout(10)
        if not self._peer_allowed():
            logger.warning("Rejected PAM request from unexpected peer")
            return
        try:
            size = struct.unpack("!I", self._receive(4))[0]
            if size == 0 or size > MAX_MESSAGE:
                return
            payload = json.loads(self._receive(size))
            response = authenticate_payload(payload, self.server.auth_config)
        except (OSError, ValueError, json.JSONDecodeError, struct.error):
            response = {"ok": False}
        data = json.dumps(response).encode()
        self.request.sendall(struct.pack("!I", len(data)) + data)

    def _peer_allowed(self) -> bool:
        credentials = self.request.getsockopt(socket.SOL_SOCKET, SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", credentials)
        return uid in {0, self.server.auth_config.web_uid}

    def _receive(self, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            chunk = self.request.recv(size - len(result))
            if not chunk:
                raise OSError("Client disconnected")
            result.extend(chunk)
        return bytes(result)


class AuthServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, config: AuthConfig) -> None:
        self.auth_config = config
        config.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        config.socket_path.unlink(missing_ok=True)
        super().__init__(str(config.socket_path), AuthRequestHandler)
        os.chown(config.socket_path, 0, config.web_gid)
        config.socket_path.chmod(0o660)

    def server_close(self) -> None:
        super().server_close()
        self.auth_config.socket_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Restricted PAM authentication broker")
    parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    try:
        config = AuthConfig.from_environment()
    except (KeyError, ValueError) as exc:
        raise SystemExit(f"invalid configuration: {exc}") from exc
    with AuthServer(config) as server:
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
