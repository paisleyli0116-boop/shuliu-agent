from __future__ import annotations

from shuliu_runtime.tools.broker import ToolResult
from shuliu_runtime.worker.protocol import WorkerMessage
from shuliu_runtime.worker.task_runner import WorkerTaskRunner


class _Connection:
    worker_id = 'worker-1'
    organization_id = 'org-1'

    def __init__(self):
        self.sent = []
        self.dispatches = []

    def send(self, message_type, payload):
        self.sent.append((message_type, dict(payload)))
        return WorkerMessage(
            message_id='no-assignment',
            request_id='request-1',
            trace_id='trace-1',
            message_type='no_assignment',
            payload={'worker_id': self.worker_id},
        )

    def dispatch_tool(self, *args, **kwargs):
        self.dispatches.append((args, kwargs))
        raise AssertionError('no Tool must run when Cloud has no assigned task')


class _Broker:
    def execute(self, *args, **kwargs):
        raise AssertionError('no Tool must run when Cloud has no assigned task')


def test_runner_polls_cloud_without_a_caller_supplied_task_id_and_handles_empty_assignment():
    connection = _Connection()
    runner = WorkerTaskRunner(connection=connection, tool_broker=_Broker())

    result = runner.run_assigned()

    assert result.message_type == 'no_assignment'
    assert connection.sent[0][0] == 'lease_request_assigned'
    assert 'task_id' not in connection.sent[0][1]
    assert connection.dispatches == []
