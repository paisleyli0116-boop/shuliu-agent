from __future__ import annotations

from threading import Event

try:
    from shuliu_runtime.worker.host import WorkerHost
except ImportError:
    WorkerHost = None
from shuliu_runtime.worker.protocol import WorkerMessage


class _Connection:
    worker_id = 'worker-1'

    def __init__(self):
        self.events = []
        self.opened = False
        self.closed = False

    def __enter__(self):
        self.opened = True
        return self

    def __exit__(self, *_):
        self.closed = True

    def send(self, message_type, payload):
        self.events.append(message_type)
        return WorkerMessage(
            message_id='heartbeat-1',
            request_id='request-1',
            trace_id='trace-1',
            message_type='heartbeat',
            payload={'worker_id': 'worker-1', 'sent_at': '2026-10-18T00:00:00+00:00'},
        )


class _Runner:
    def __init__(self, stop_event):
        self.stop_event = stop_event
        self.calls = 0

    def run_assigned(self):
        self.calls += 1
        self.stop_event.set()
        return WorkerMessage(
            message_id='no-assignment',
            request_id='request-2',
            trace_id='trace-2',
            message_type='no_assignment',
            payload={'worker_id': 'worker-1'},
        )


def test_worker_host_authenticates_then_heartbeats_and_polls_cloud_assignment():
    assert WorkerHost is not None, 'production WorkerHost is not implemented'
    stop_event = Event()
    connection = _Connection()
    runner = _Runner(stop_event)
    host = WorkerHost(connection=connection, runner=runner, idle_poll_seconds=0.01)

    host.run(stop_event=stop_event)

    assert connection.opened and connection.closed
    assert connection.events == ['heartbeat']
    assert runner.calls == 1


def test_worker_host_production_factory_binds_the_fenced_task_runner():
    from shuliu_runtime.worker.task_runner import WorkerTaskRunner

    class RunnableConnection(_Connection):
        def dispatch_tool(self, *args, **kwargs):
            raise AssertionError('dispatch is not expected during host construction')

    class ToolBroker:
        def execute(self, *args, **kwargs):
            raise AssertionError('Tool execution is not expected during host construction')

    assert hasattr(WorkerHost, 'with_tool_broker'), (
        'production Worker host does not compose the fenced task runner'
    )
    host = WorkerHost.with_tool_broker(
        connection=RunnableConnection(),
        tool_broker=ToolBroker(),
        idle_poll_seconds=0.1,
    )
    assert isinstance(host._runner, WorkerTaskRunner)
