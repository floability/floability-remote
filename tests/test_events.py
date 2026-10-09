import threading
import unittest

from floability_remote.events import (
    Emitter,
    Event,
    EventKind,
    FanoutSink,
    ListSink,
    RedactingSink,
    Redactor,
)
from floability_remote.interaction import CancelToken


class EventTests(unittest.TestCase):
    def test_event_is_json_ready(self):
        event = Event(EventKind.STEP, "Connecting", step=1, total=5)
        self.assertEqual(
            {key: value for key, value in event.to_dict().items() if key != "timestamp"},
            {"kind": "step", "message": "Connecting", "step": 1, "total": 5, "data": {}},
        )

    def test_progress_is_reported_once(self):
        sink = ListSink()
        emitter = Emitter(sink)
        emitter.progress("Starting JupyterLab...")
        emitter.progress("Starting JupyterLab...")
        self.assertEqual(sink.kinds(), [EventKind.PROGRESS])

    def test_log_block_adds_trailing_newline(self):
        sink = ListSink()
        Emitter(sink).log_block("line one\nline two")
        self.assertEqual(sink.events[0].message, "line one\nline two\n")

    def test_fanout_delivers_to_every_sink(self):
        first, second = ListSink(), ListSink()
        FanoutSink((first, second)).emit(Event(EventKind.DETAIL, "hello"))
        self.assertEqual(first.kinds(), ["detail"])
        self.assertEqual(second.kinds(), ["detail"])


class RedactionTests(unittest.TestCase):
    def test_logs_are_redacted_but_ready_link_is_kept(self):
        redactor = Redactor()
        redactor.add("secret-token")
        sink = ListSink()
        redacting = RedactingSink(sink, redactor)

        redacting.emit(Event(EventKind.LOG, "url?token=secret-token\n"))
        redacting.emit(
            Event(EventKind.DETAIL, "info", data={"note": "secret-token", "port": 1})
        )
        redacting.emit(
            Event(EventKind.READY, "ready", data={"url": "url?token=secret-token"})
        )

        self.assertEqual(sink.events[0].message, "url?token=[REDACTED]\n")
        self.assertEqual(sink.events[1].data, {"note": "[REDACTED]", "port": 1})
        self.assertEqual(sink.events[2].data["url"], "url?token=secret-token")

    def test_longer_secrets_are_replaced_first(self):
        redactor = Redactor()
        redactor.add("abc")
        redactor.add("abcdef")
        self.assertEqual(redactor.redact("abcdef"), "[REDACTED]")

    def test_empty_secret_is_ignored(self):
        redactor = Redactor()
        redactor.add("")
        self.assertEqual(redactor.redact("text"), "text")


class CancelTokenTests(unittest.TestCase):
    def test_callbacks_run_once(self):
        token = CancelToken()
        calls = []
        token.on_cancel(lambda: calls.append("first"))
        token.cancel()
        token.cancel()
        self.assertTrue(token.cancelled)
        self.assertEqual(calls, ["first"])

    def test_callback_registered_after_cancel_runs_immediately(self):
        token = CancelToken()
        token.cancel()
        calls = []
        token.on_cancel(lambda: calls.append("late"))
        self.assertEqual(calls, ["late"])

    def test_wait_returns_when_cancelled_from_another_thread(self):
        token = CancelToken()
        threading.Timer(0.01, token.cancel).start()
        self.assertTrue(token.wait(timeout=2))


if __name__ == "__main__":
    unittest.main()
