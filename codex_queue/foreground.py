"""Foreground controller delegating every scheduling decision to Worker.once."""
import os
import queue
import threading
import time
from .store import Store, QueueError, WriterBusy
from .worker import Worker
from .execution import runner_lease


class Foreground:
    def __init__(self, state, provider, config, simulation=False):
        if not simulation and config.get('execution_enabled') is not True:
            raise QueueError('Real execution is disabled. Enable it explicitly in Settings first.')
        if simulation and getattr(provider, 'live', True):
            raise QueueError('Simulation requires fixture usage.')
        if not simulation and not getattr(provider, 'live', False):
            raise QueueError('Fixtures cannot authorize real execution.')
        interval = config.get('poll_interval_seconds', 60)
        if type(interval) is not int or not 10 <= interval <= 3600:
            raise QueueError('poll interval must be between 10 and 3600 seconds')
        self.state, self.provider, self.config, self.simulation = state, provider, config, simulation
        self.events = queue.Queue(maxsize=2048)
        self.stop_event = threading.Event()
        self.worker = None
        self.thread = None

    def emit(self, event):
        # Bound UI memory. Disk logs remain complete even if rendering falls behind.
        try:
            self.events.put_nowait(event)
        except queue.Full:
            try: self.events.get_nowait()
            except queue.Empty: pass
            self.events.put_nowait(event)

    def start(self):
        if self.thread and self.thread.is_alive():
            raise QueueError('Foreground watcher is already running.')
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.loop, name='queue-foreground', daemon=False)
        self.thread.start()

    def loop(self):
        store = Store(self.state)
        try:
            with runner_lease(self.state, "foreground") as lease:
                self.run_cycles(store, lease)
        except (QueueError, OSError, ValueError) as error:
            self.emit({"state":"blocked", "reason":str(error)})
        finally:
            store.close()
            self.emit({"state":"stopped", "reason":"Monitoring stopped; partial work and logs preserved."})

    def run_cycles(self, store, lease):
        self.worker = Worker(store, self.provider, self.config.get('codex', 'codex'), self.simulation,
                             self.config.get('job_timeout_seconds', 3600), self.emit,
                             self.config.get('poll_interval_seconds', 60), config=self.config)
        self.worker.monitor_idle = True
        self.worker.extra_fds = () if os.name == "nt" else (lease.fileno(),)
        while not self.stop_event.is_set():
            try:
                result = self.worker.once()
            except WriterBusy:
                result = {'state': 'waiting', 'reason': 'Waiting for another active job'}
                store.backoff(time.time(), interval=self.config.get('poll_interval_seconds', 60))
            except (QueueError, OSError, ValueError) as error:
                result = {'state': 'blocked', 'reason': str(error)}
            self.emit(result)
            self.stop_event.wait(max(1, min(60, store.next_poll() - time.time())))


    def stop(self):
        self.stop_event.set()
        if self.worker:
            self.worker.stop()

    def join(self):
        if self.thread:
            self.thread.join()  # UI awaits this off its event loop; never silently abandons a child.
