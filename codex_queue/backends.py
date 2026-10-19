"""Optional execution adapters. Scheduling remains in Worker."""
import subprocess
from .store import QueueError


class BackendUnavailable(QueueError):
    """A selected backend cannot dispatch; leave the prompt queued."""


class DirectBackend:
    name = 'direct'
    persistent = False

    def start(self, command, **options):
        return subprocess.Popen(command, **options)


def resolve(store, config, job):
    name = job.get('backend', 'default')
    if name == 'default':
        name = 'herdr' if config.get('herdr_enabled') is True else 'direct'
    if name == 'direct':
        return DirectBackend()
    if name == 'herdr':
        from .herdr import HerdrBackend
        return HerdrBackend(store, config)
    raise BackendUnavailable('Unknown execution backend; select Direct Codex or Herdr.')


EXTENSIONS = {'herdr': {'name': 'Herdr', 'description': 'Run visible jobs in managed terminal tabs'}}
