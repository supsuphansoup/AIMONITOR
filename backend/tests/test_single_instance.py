"""A second desktop launch must hand off to the running app, not open another window."""
import socket
import threading

from desktop.single_instance import SingleInstance


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_second_instance_is_refused_and_asks_the_first_to_show():
    port = _free_port()
    first = SingleInstance(port=port)
    shown = threading.Event()
    first.on_show = shown.set
    try:
        assert first.acquire()
        second = SingleInstance(port=port)
        assert not second.acquire()
        assert second.notify_running()
        assert shown.wait(2.0)
    finally:
        first.release()


def test_port_is_free_again_after_release():
    port = _free_port()
    first = SingleInstance(port=port)
    assert first.acquire()
    first.release()
    again = SingleInstance(port=port)
    try:
        assert again.acquire()
    finally:
        again.release()


def test_unrelated_message_does_not_trigger_show():
    port = _free_port()
    inst = SingleInstance(port=port)
    shown = threading.Event()
    inst.on_show = shown.set
    try:
        assert inst.acquire()
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as c:
            c.sendall(b"GET / HTTP/1.1\r\n\r\n")
        assert not shown.wait(0.5)
    finally:
        inst.release()
