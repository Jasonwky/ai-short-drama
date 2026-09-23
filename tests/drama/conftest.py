# -*- coding: utf-8 -*-
import pytest

from tools.drama.db import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()
