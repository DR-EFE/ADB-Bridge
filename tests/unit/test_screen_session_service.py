import io
import subprocess
import threading
import time

import pytest

from ui.services.screen_session_service import (
    SCREEN_PRESETS,
    ScreenPreset,
    ScreenSessionError,
    ScreenSessionService,
    ScreenSessionState,
)


class FakeAdb:
    adb_executable = r"C:\Android\platform-tools\adb.exe"

    def __init__(self, ready=True):
        self.ready = ready
        self.resolved = []

    def resolve_serial(self, serial):
        self.resolved.append(serial)
        if not self.ready:
            raise RuntimeError("el dispositivo no está en estado device")
        return serial


class FakeProcess:
    def __init__(self, output="", returncode=None):
        self.stdout = io.StringIO(output)
        self.returncode = returncode
        self._done = threading.Event()
        if returncode is not None:
            self._done.set()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("scrcpy", timeout)
        return self.returncode

    def terminate(self):
        self.returncode = 0
        self._done.set()

    def kill(self):
        self.returncode = -9
        self._done.set()

    def finish(self, returncode=0):
        self.returncode = returncode
        self._done.set()


def make_service(tmp_path, *, processes=None, adb=None, which=None, confirmation=0.01, output="scrcpy log line\n"):
    executable = tmp_path / "tools" / "scrcpy" / "scrcpy.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fake executable")
    launched = processes if processes is not None else []

    def popen(command, **kwargs):
        process = FakeProcess(output)
        launched.append((command, kwargs, process))
        return process

    service = ScreenSessionService(
        adb_service=adb or FakeAdb(),
        app_root=tmp_path,
        popen_factory=popen,
        which=which or (lambda _name: None),
        startup_confirmation_seconds=confirmation,
    )
    return service, launched, executable


def wait_for_state(service, serial, expected, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session = service.get_session(serial)
        if session and session.state == expected:
            return session
        time.sleep(0.005)
    pytest.fail(f"La sesión no llegó al estado {expected}")


def test_builds_expected_commands_for_profiles_and_options(tmp_path):
    service, _, _ = make_service(tmp_path)

    assert service.build_command("phone", ScreenPreset.LOW) == [
        "-s", "phone", "--max-size", "800", "--max-fps", "30",
        "--video-bit-rate", "4M", "--no-audio", "--stay-awake",
    ]
    assert service.build_command("phone", "medium", turn_screen_off=True)[-1] == "--turn-screen-off"
    high_command = service.build_command("phone", ScreenPreset.HIGH)
    assert high_command[3] == "1920"
    assert "12M" in high_command and "--no-audio" in high_command
    assert SCREEN_PRESETS[ScreenPreset.NATIVE].max_size == 0


def test_bundled_executable_is_preferred_and_session_uses_explicit_serial(tmp_path):
    adb = FakeAdb()
    service, launched, bundled = make_service(tmp_path, adb=adb, which=lambda _: "C:/external/scrcpy.exe")

    session = service.start("phone-123", "medium")

    assert session.state == ScreenSessionState.STARTING
    assert launched[0][0][0] == str(bundled)
    assert launched[0][0][1:3] == ["-s", "phone-123"]
    assert launched[0][1]["env"]["ADB"] == adb.adb_executable
    assert adb.resolved == ["phone-123"]
    assert wait_for_state(service, "phone-123", ScreenSessionState.RUNNING)
    service.shutdown()


def test_missing_executable_mentions_bundled_path_and_path(tmp_path):
    service = ScreenSessionService(
        adb_service=FakeAdb(), app_root=tmp_path, which=lambda _: None,
    )

    with pytest.raises(ScreenSessionError) as exc_info:
        service.resolve_executable()

    assert "tools/scrcpy/scrcpy.exe" in str(exc_info.value)
    assert "PATH" in str(exc_info.value)


def test_path_executable_is_used_when_bundled_copy_is_absent(tmp_path):
    external = tmp_path / "external" / "scrcpy.exe"
    external.parent.mkdir()
    external.write_bytes(b"external executable")
    launched = []

    def popen(command, **kwargs):
        process = FakeProcess()
        launched.append((command, kwargs, process))
        return process

    service = ScreenSessionService(
        adb_service=FakeAdb(),
        app_root=tmp_path,
        popen_factory=popen,
        which=lambda _name: str(external),
        startup_confirmation_seconds=0.01,
    )

    service.start("phone")

    assert launched[0][0][0] == str(external)
    service.shutdown()


def test_unready_device_is_rejected_without_spawning(tmp_path):
    service, launched, _ = make_service(tmp_path, adb=FakeAdb(ready=False))

    with pytest.raises(ScreenSessionError, match="estado device"):
        service.start("phone")

    assert launched == []


def test_startup_failure_keeps_diagnostics_for_retry(tmp_path):
    service, launched, _ = make_service(tmp_path, output="ERROR: device unauthorized\n", confirmation=0.2)
    session = service.start("phone")
    launched[0][2].finish(returncode=1)

    failed = wait_for_state(service, "phone", ScreenSessionState.ERROR)

    assert "código 1" in failed.error
    assert "device unauthorized" in failed.error
    assert service.start("phone") is not session
    service.shutdown()


def test_duplicate_session_is_rejected(tmp_path):
    service, _, _ = make_service(tmp_path)
    service.start("phone")
    wait_for_state(service, "phone", ScreenSessionState.RUNNING)

    with pytest.raises(ScreenSessionError, match="Ya hay una sesión"):
        service.start("phone")
    service.shutdown()


def test_early_process_exit_during_starting_is_an_error_and_can_retry(tmp_path):
    processes = []
    service, launched, _ = make_service(tmp_path, processes=processes, confirmation=0.2)

    session = service.start("phone")
    launched[0][2].finish(returncode=0)
    ended = wait_for_state(service, "phone", ScreenSessionState.ERROR)

    assert ended.error and "durante el inicio" in ended.error
    replacement = service.start("phone")
    assert replacement is not session
    service.shutdown()


def test_manual_close_frees_session_and_output_reader_stops(tmp_path):
    service, launched, _ = make_service(tmp_path)
    session = service.start("phone")
    wait_for_state(service, "phone", ScreenSessionState.RUNNING)
    launched[0][2].finish(returncode=0)
    wait_for_state(service, "phone", ScreenSessionState.STOPPED)

    replacement = service.start("phone")
    assert replacement is not session
    assert session.reader_thread is not None
    session.reader_thread.join(timeout=0.2)
    assert not session.reader_thread.is_alive()
    service.shutdown()


def test_shutdown_terminates_only_service_owned_processes_and_joins_threads(tmp_path):
    service, launched, _ = make_service(tmp_path)
    session = service.start("phone")
    wait_for_state(service, "phone", ScreenSessionState.RUNNING)

    service.shutdown()

    assert launched[0][2].returncode == 0
    assert session.state == ScreenSessionState.STOPPED
    assert session.reader_thread is not None and not session.reader_thread.is_alive()
    assert session.monitor_thread is not None and not session.monitor_thread.is_alive()


def test_output_buffer_is_bounded(tmp_path):
    lines = "".join(f"line {index}\n" for index in range(250))
    service, _, _ = make_service(tmp_path, output=lines)
    session = service.start("phone")
    wait_for_state(service, "phone", ScreenSessionState.RUNNING)
    session.reader_thread.join(timeout=0.5)

    assert len(session.logs) == 200
    assert session.logs[0] == "line 50"
    service.shutdown()
