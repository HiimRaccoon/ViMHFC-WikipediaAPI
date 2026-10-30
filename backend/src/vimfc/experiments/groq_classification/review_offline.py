"""Block external connections while permitting Windows asyncio's self-pipe."""
from contextlib import contextmanager
import _socket
import socket
import sys
from unittest.mock import patch


def _denied(*args, **kwargs):
    raise RuntimeError('Human review is offline; outbound networking is disabled')


def _connect(sock, address):
    # CPython on Windows emulates socketpair with a loopback connection. Check
    # its code object as well as the literal loopback address; arbitrary local
    # HTTP clients remain blocked. Native Unix socketpair needs no exemption.
    fallback = getattr(socket, '_fallback_socketpair', None)
    if (fallback is not None and sys._getframe(1).f_code is fallback.__code__
            and isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1')):
        return _socket.socket.connect(sock, address)
    return _denied()


@contextmanager
def offline_review_network():
    with patch.object(socket.socket, 'connect', _connect), patch.object(socket.socket, 'connect_ex', _denied), patch.object(socket, 'getaddrinfo', _denied):
        yield
