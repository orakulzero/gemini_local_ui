"""
backend/app/mcp/win32_job.py
Windows Job Object guard ensuring orphaned child subprocesses terminate on parent exit.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

logger = logging.getLogger("gemini_mcp.win32_job")

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryLimit", ctypes.c_size_t),
            ("PeakJobMemoryLimit", ctypes.c_size_t),
        ]


class Win32JobGuard:
    def __init__(self) -> None:
        self.job_handle: Optional[int] = None
        if IS_WINDOWS:
            try:
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.CreateJobObjectW(None, None)
                if handle:
                    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
                    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                    success = kernel32.SetInformationJobObject(
                        handle,
                        JobObjectExtendedLimitInformation,
                        ctypes.byref(info),
                        ctypes.sizeof(info),
                    )
                    if success:
                        self.job_handle = handle
            except Exception as e:
                logger.debug("Could not initialize Win32JobGuard: %s", e)

    def assign_pid(self, pid: int) -> bool:
        if not IS_WINDOWS or not self.job_handle:
            return False
        try:
            kernel32 = ctypes.windll.kernel32
            h_proc = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
            if h_proc:
                ok = bool(kernel32.AssignProcessToJobObject(self.job_handle, h_proc))
                kernel32.CloseHandle(h_proc)
                return ok
        except Exception as e:
            logger.debug("Failed assigning PID %s to Win32JobGuard: %s", pid, e)
        return False

    def close(self) -> None:
        if IS_WINDOWS and self.job_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(self.job_handle)
            except Exception:
                pass
            self.job_handle = None
