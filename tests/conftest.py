import pytest

from twy.calendars import clear_cache
from twy.db import connect
from twy.repo import Repo


@pytest.fixture
def repo() -> Repo:
    return Repo(connect(":memory:"))


@pytest.fixture(autouse=True)
def fresh_calendar_cache():
    """Imported calendars are cached per process; each test starts without any."""
    clear_cache()
    yield
    clear_cache()
