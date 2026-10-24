"""Minimal stand-in for python-can, backed by tests/sim_car.py."""
import queue

import sim_car


class CanError(Exception):
    pass


class Message:
    def __init__(self, arbitration_id=0, data=b"", is_extended_id=False,
                 is_fd=False, check=False):
        self.arbitration_id = arbitration_id
        self.data = bytearray(data)
        self.is_extended_id = is_extended_id
        self.is_fd = is_fd


class Bus:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.q = queue.Queue()

    def send(self, msg, timeout=None):
        payload = bytes(msg.data[1:1 + msg.data[0]])
        ext = msg.is_extended_id
        if msg.arbitration_id in (0x7DF, 0x18DB33F1):
            for rx, r in sim_car.functional(ext, payload):
                self.q.put(Message(rx, bytes([len(r)]) + r, ext))
            return
        r = sim_car.respond(ext, msg.arbitration_id, payload)
        if r:
            rx = sim_car.MODS[(ext, msg.arbitration_id)]["rx"]
            self.q.put(Message(rx, bytes([len(r)]) + r, ext))

    def recv(self, timeout=None):
        try:
            return self.q.get(timeout=timeout) if timeout else self.q.get_nowait()
        except queue.Empty:
            return None

    def shutdown(self):
        pass
