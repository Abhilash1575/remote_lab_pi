"""Translates JSON commands from the browser (relayed through the Master —
see app.py's `debug_command` handler) into GdbSession calls, and normalizes
GdbSession replies into the `debug_event` shapes the frontend expects.

Client -> server commands: continue, step, step_over, pause, reset,
set_breakpoint, remove_breakpoint, read_memory, read_registers, disassemble,
read_locals, add_watch, remove_watch, update_watches.

Server -> client events: stopped, registers, memory, disasm, breakpoint,
locals, watch, watch_update, console, error.

Ported from the standalone Debugger/ prototype (backend/app/ws_protocol.py).
`handle_command` was `async def` there (GdbSession's methods were themselves
async); the whole file is now a plain synchronous function to match the
eventlet-native port of GdbSession (see gdb_bridge.py) — behavior is
otherwise unchanged.
"""

from __future__ import annotations

from typing import Any, Protocol


class GdbLike(Protocol):
    def cont(self) -> None: ...
    def step(self) -> None: ...
    def step_over(self) -> None: ...
    def pause(self) -> None: ...
    def reset(self, halt: bool = True) -> None: ...
    def set_breakpoint(self, addr: str) -> dict[str, Any]: ...
    def remove_breakpoint(self, bp_id: int) -> dict[str, Any]: ...
    def read_registers(self) -> dict[str, str]: ...
    def read_memory(self, addr: str, length: int) -> dict[str, Any]: ...
    def disassemble(self, addr: str, count: int) -> list[dict[str, Any]]: ...
    def read_locals(self) -> list[dict[str, Any]]: ...
    def add_watch(self, expr: str) -> dict[str, Any]: ...
    def remove_watch(self, name: str) -> None: ...
    def update_watches(self) -> list[dict[str, Any]]: ...


# Commands that only trigger a later async "stopped" event via on_event.
_FIRE_AND_FORGET = {"continue", "step", "step_over", "pause", "reset"}


def handle_command(session: GdbLike, msg: dict[str, Any]) -> dict[str, Any] | None:
    cmd = msg.get("cmd")

    if cmd == "continue":
        session.cont()
        return None
    if cmd == "step":
        session.step()
        return None
    if cmd == "step_over":
        session.step_over()
        return None
    if cmd == "pause":
        session.pause()
        return None
    if cmd == "reset":
        session.reset()
        return None

    if cmd == "set_breakpoint":
        resp = session.set_breakpoint(msg["addr"])
        bkpt = resp.get("payload", {}).get("bkpt", {})
        return {"event": "breakpoint", "action": "set", "id": bkpt.get("number"), "addr": bkpt.get("addr")}

    if cmd == "remove_breakpoint":
        session.remove_breakpoint(int(msg["id"]))
        return {"event": "breakpoint", "action": "removed", "id": msg["id"]}

    if cmd == "read_registers":
        values = session.read_registers()
        return {"event": "registers", "values": values}

    if cmd == "read_memory":
        result = session.read_memory(msg["addr"], int(msg["length"]))
        return {"event": "memory", **result}

    if cmd == "disassemble":
        lines = session.disassemble(msg["addr"], int(msg.get("count", 20)))
        return {"event": "disasm", "lines": lines}

    if cmd == "read_locals":
        variables = session.read_locals()
        return {"event": "locals", "variables": variables}

    if cmd == "add_watch":
        result = session.add_watch(msg["expr"])
        if "error" in result:
            return {"event": "error", "message": result["error"]}
        return {"event": "watch", "action": "added", **result}

    if cmd == "remove_watch":
        session.remove_watch(msg["name"])
        return {"event": "watch", "action": "removed", "name": msg["name"]}

    if cmd == "update_watches":
        changes = session.update_watches()
        return {"event": "watch_update", "changes": changes}

    return {"event": "error", "message": f"unknown command: {cmd!r}"}


def is_fire_and_forget(cmd: str) -> bool:
    return cmd in _FIRE_AND_FORGET
