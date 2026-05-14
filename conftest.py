import pytest

def pytest_configure(config):
    config.addinivalue_line("markers", "integration: full browser round-trip tests")
