import os
import sys

HERE = os.path.dirname(__file__)
# Mocks first, so the scanner imports the simulated can/isotp, not real ones.
sys.path.insert(0, os.path.join(HERE, "mocks"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import pytest  # noqa: E402

import sim_car  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_car():
    sim_car.reset()
    yield
