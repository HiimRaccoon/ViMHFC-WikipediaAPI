"""All default tests are offline; an accidental HTTP socket is a test failure."""
import socket
import pytest


def pytest_addoption(parser):
    parser.addoption('--run-network',action='store_true',default=False,
                     help='Explicitly opt into marked source-network checks; never used by default verification')


@pytest.fixture(autouse=True)
def deny_network(monkeypatch,request):
    if request.node.get_closest_marker('network'):
        if not request.config.getoption('--run-network'):
            pytest.skip('Real source acquisition requires explicit --run-network')
        return
    def denied(*args, **kwargs):
        raise AssertionError("Real network access is forbidden in fixture tests")
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture
def config():
    from vimfc.config import load_config
    return load_config("configs/pipeline.json")
