import pytest

from twy.db import connect
from twy.repo import Repo


@pytest.fixture
def repo() -> Repo:
    return Repo(connect(":memory:"))
