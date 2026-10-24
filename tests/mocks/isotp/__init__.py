"""Minimal stand-in for can-isotp 2.x, backed by tests/sim_car.py."""
import queue

import sim_car


class AddressingMode:
    Normal_11bits = "11"
    Normal_29bits = "29"


class Address:
    def __init__(self, mode, txid, rxid):
        self.ext = mode == AddressingMode.Normal_29bits
        self.txid = txid
        self.rxid = rxid


class CanStack:
    def __init__(self, bus, address, params, error_handler=None):
        assert params["tx_data_length"] in (8, 64)
        self.address = address
        self.q = queue.Queue()

    def start(self):
        pass

    def stop(self):
        pass

    def send(self, payload):
        r = sim_car.respond(self.address.ext, self.address.txid, bytes(payload))
        if r is not None:
            self.q.put(r)

    def recv(self, block=False, timeout=None):
        try:
            return self.q.get(timeout=timeout) if block else self.q.get_nowait()
        except queue.Empty:
            return None
