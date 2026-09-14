"""Single-process isolation for bounded evidence parsing.

Operational deadlines abort a caller; they never become cached parse evidence.
Spawn and forced process reaping depend on the OS and are not hard real-time
bounds. Cleanup waits are bounded; if the OS refuses cleanup, close() reports
failure and retains unfinished resources for retry. Library entry points must
use the multiprocessing __main__ guard.
"""

from __future__ import annotations

from math import isfinite
import multiprocessing
from multiprocessing.connection import Connection
import threading
from time import monotonic

from . import evidence, model
from .evidence import EvidenceBundle, EvidenceLimits
from .model import IndexContractError


class EvidenceWorkerError(RuntimeError):
    """An operational failure, never a deterministic parse outcome."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _serve(connection: Connection) -> None:
    """Serve plain tuples over a trusted, same-user multiprocessing pipe."""
    try:
        while True:
            request = connection.recv()
            if type(request) is not tuple or len(request) != 5 or request[0] != "parse":
                connection.send(("worker_failed", "malformed evidence request"))
                return
            _, source, path, object_format, limits = request
            try:
                bundle = evidence.parse_evidence(source, path, object_format, limits)
            except IndexContractError as error:
                connection.send(("contract_error", error.code, str(error)))
                return
            except Exception as error:
                connection.send(("worker_failed", str(error)))
                return
            connection.send(("success", bundle))
    except (EOFError, OSError):
        pass
    finally:
        connection.close()


class EvidenceWorker:
	"""Lazily reuse one spawn child, serializing parse calls."""

	def __init__(self, timeout_seconds: float = 5.0) -> None:
		try:
			valid = (type(timeout_seconds) in (int, float)
					 and timeout_seconds > 0 and isfinite(timeout_seconds))
		except OverflowError:
			valid = False
		if not valid:
			raise IndexContractError("invalid_limits", "deadline must be finite and positive, excluding bool")
		self._timeout_seconds = float(timeout_seconds)
		self._lock = threading.RLock()
		self._closed = False
		self._process = None
		self._connection: Connection | None = None
		self._exchange: threading.Thread | None = None

	def __enter__(self) -> EvidenceWorker:
		return self

	def __exit__(self, exc_type, exc_value, traceback) -> None:
		try:
			self.close()
		except BaseException as cleanup_error:
			if exc_value is None:
				raise
			exc_value.add_note(f"Evidence worker cleanup failed: {cleanup_error!r}")

	def _start(self) -> None:
		context = multiprocessing.get_context("spawn")
		self._connection, child_connection = context.Pipe()
		try:
			self._process = context.Process(target=_serve, args=(child_connection,))
			self._process.start()
		finally:
			child_connection.close()

	def parse(self, source: bytes, path: bytes, object_format: str = "sha1",
			  limits: EvidenceLimits = EvidenceLimits()) -> EvidenceBundle:
		with self._lock:
			if self._closed:
				raise EvidenceWorkerError("worker_closed", "evidence worker is closed")
			try:
				if type(source) is not bytes:
					raise IndexContractError("invalid_source", "source must be bytes")
				model._path(path)
				if type(object_format) is not str or object_format not in ("sha1", "sha256"):
					raise IndexContractError("invalid_object_format", "expected sha1 or sha256")
				evidence._validate_limits(limits)
				deadline = monotonic() + self._timeout_seconds
				if self._process is None:
					self._start()
				connection = self._connection
				if connection is None:
					raise EvidenceWorkerError("worker_failed", "evidence pipe is unavailable")
				responses: list[object] = []
				failures: list[BaseException] = []
				completed_at: float | None = None

				def exchange() -> None:
					nonlocal completed_at
					try:
						connection.send(("parse", source, path, object_format, limits))
						responses.append(connection.recv())
					except BaseException as error:
						failures.append(error)
					finally:
						completed_at = monotonic()

				thread = threading.Thread(target=exchange, name="gita-evidence-io")
				self._exchange = thread
				thread.start()
				thread.join(min(threading.TIMEOUT_MAX, max(0.0, deadline - monotonic())))
				if thread.is_alive() or completed_at is None or completed_at > deadline:
					raise EvidenceWorkerError("resource_limit", "evidence worker deadline exceeded")
				self._exchange = None
				if failures:
					raise EvidenceWorkerError("worker_failed", "evidence transport failed") from failures[0]
				response = responses[0]
				if type(response) is tuple:
					if (len(response) == 2 and response[0] == "success"
							and type(response[1]) is EvidenceBundle):
						return response[1]
					if (len(response) == 3 and response[0] == "contract_error"
							and type(response[1]) is str and type(response[2]) is str):
						raise IndexContractError(response[1], response[2])
					if (len(response) == 2 and response[0] == "worker_failed"
							and type(response[1]) is str):
						raise EvidenceWorkerError("worker_failed", response[1])
				raise EvidenceWorkerError("worker_failed", "malformed evidence response")
			except BaseException as error:
				try:
					self.close()
				except BaseException as cleanup_error:
					error.add_note(f"Evidence worker cleanup failed: {cleanup_error!r}")
				if isinstance(error, (IndexContractError, EvidenceWorkerError)) or not isinstance(error, Exception):
					raise
				raise EvidenceWorkerError("worker_failed", "evidence worker exchange failed") from error

	def close(self) -> None:
		"""Fail closed with bounded waits, retaining unfinished resources for retry."""
		with self._lock:
			self._closed = True
			process = self._process
			thread = self._exchange
			connection = self._connection
			failures: list[BaseException] = []
			if process is not None:
				try:
					if process.pid is None:
						self._process = None
					else:
						joined = False
						if process.is_alive():
							try:
								process.terminate()
							except BaseException as error:
								failures.append(error)
						try:
							process.join(1.0)
							joined = True
						except BaseException as error:
							failures.append(error)
						if process.is_alive():
							try:
								process.kill()
							except BaseException as error:
								failures.append(error)
							try:
								process.join(1.0)
								joined = True
							except BaseException as error:
								failures.append(error)
						if joined and not process.is_alive() and process.exitcode is not None:
							self._process = None
				except BaseException as error:
					failures.append(error)
			if thread is not None:
				try:
					if thread.ident is not None:
						thread.join(1.0)
					if not thread.is_alive():
						self._exchange = None
				except BaseException as error:
					failures.append(error)
			if connection is not None:
				try:
					connection.close()
					self._connection = None
				except BaseException as error:
					failures.append(error)
			if self._exchange is not None and thread is not None:
				try:
					thread.join(1.0)
					if not thread.is_alive():
						self._exchange = None
				except BaseException as error:
					failures.append(error)
			if self._process is not None or self._exchange is not None or self._connection is not None:
				cleanup_error = EvidenceWorkerError(
					"worker_failed", "evidence worker cleanup incomplete; call close() again",
				)
				for failure in failures:
					cleanup_error.add_note(repr(failure))
				raise cleanup_error from (failures[0] if failures else None)
			for failure in failures:
				if not isinstance(failure, Exception):
					raise failure