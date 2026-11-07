# -*- coding: utf-8 -*-
import pytest

from tools.refs.db import RefStore


@pytest.fixture
def refs():
    s = RefStore(":memory:")
    yield s
    s.close()
