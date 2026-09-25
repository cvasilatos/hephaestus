from __future__ import annotations

import grp
import os
import pwd
from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class SystemIdentity:
    username: str
    uid: int
    gid: int
    shell: str


def get_interactive_identity(username: str) -> SystemIdentity | None:
    try:
        entry = pwd.getpwnam(username)
    except KeyError:
        return None
    blocked_shells = {"/usr/sbin/nologin", "/sbin/nologin", "/bin/false", ""}
    if entry.pw_uid == 0 or entry.pw_uid < settings.MIN_INTERACTIVE_UID:
        return None
    if entry.pw_shell in blocked_shells:
        return None
    return SystemIdentity(
        username=entry.pw_name, uid=entry.pw_uid, gid=entry.pw_gid, shell=entry.pw_shell
    )


def is_os_admin(username: str) -> bool:
    try:
        identity = pwd.getpwnam(username)
        admin_group = grp.getgrnam(settings.ADMIN_GROUP)
        return admin_group.gr_gid in os.getgrouplist(username, identity.pw_gid)
    except (KeyError, OSError):
        return False
