import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest

from dofus_control import keys


@pytest.fixture(autouse=True)
def us_keymap():
    """Tests are layout independent: "auto" resolves to a US keymap regardless of the host Mac."""
    keys.set_detected_keymap(dict(keys.US_KEYCODES), {})
    yield
    keys.set_detected_keymap(None)
