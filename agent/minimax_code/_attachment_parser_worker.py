"""Trusted stdlib-only bootstrap, invoked with -I -B -S; gate before input/imports.

Resource limits control consumption, not filesystem or network access. Only the
fixed DOCX/PDF/XLSX parsers load after limits and service lifetime are installed.
"""
from __future__ import annotations

import os
import sys

MEMORY = 512 * 1024 * 1024
CPU = 10


def establish_limits():
    if sys.platform == "linux":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
        resource.setrlimit(resource.RLIMIT_CPU, (CPU, CPU))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        import ctypes

        if len(sys.argv) != 5 or int(sys.argv[1]) != os.getppid():
            raise RuntimeError("Parser parent changed")
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, 9, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG, SIGKILL
            raise OSError("Cannot bind parser lifetime")
        if int(sys.argv[1]) != os.getppid():
            raise RuntimeError("Parser parent changed during lifetime binding")
        return None
    if sys.platform != "win32":
        raise RuntimeError("Unsupported parser host")
    import ctypes
    from ctypes import wintypes

    # Import only this trusted stdlib helper, before site/third-party imports.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _windows_process_host import ExtendedLimits, watch_parent

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimits()
    # JOB_TIME | PROCESS_MEMORY | ACTIVE_PROCESS | KILL_ON_JOB_CLOSE.
    limits.Basic.Flags = 0x4 | 0x100 | 0x8 | 0x2000
    limits.Basic.JobTime = CPU * 10_000_000
    limits.Basic.ActiveProcesses = 1
    limits.ProcessMemory = MEMORY
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    if len(sys.argv) != 6:
        raise RuntimeError("Missing parser parent identity")
    watch_parent(int(sys.argv[1]), int(sys.argv[2]))
    return job  # Sole noninherited handle is retained until process exit.


def main():
    try:
        if len(sys.argv) < 4 or sys.argv[-3] not in {"docx", "pdf", "xlsx", "xlsx_extract", "xlsx_list", "pdf_page", "docx_segment", "ocr_image"}:
            raise RuntimeError("Invalid fixed parser kind")
        job = establish_limits()
    except Exception:
        # Static exit code, no exception details or imported application config.
        return 126
    assert job or sys.platform == "linux"
    # Fixed paths computed by the service's sysconfig, never user package paths
    # or startup hooks; no site.main(), .pth, PYTHONPATH or user site processing.
    sys.path.extend(sys.argv[-2:])
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import json

    data = sys.stdin.buffer.read(10 * 1024 * 1024 + 256)
    if len(data) > 10 * 1024 * 1024 + 255:
        return 127
    if sys.argv[-3] == "ocr_image":
        from minimax_code.attachment_ocr import recognize_image

        if len(data) < 3 or len(data) - 2 > 10 * 1024 * 1024:
            return 127
        result = recognize_image(data[2:], data[0], data[1])
    elif sys.argv[-3] in {"pdf_page", "docx_segment"}:
        from minimax_code.attachment_document_page import read_docx_segment, read_pdf_page

        if len(data) < 8 or len(data) - 8 > 10 * 1024 * 1024:
            return 127
        unit, offset = int.from_bytes(data[:4], "big"), int.from_bytes(data[4:8], "big")
        if not 1 <= unit <= 10000 or offset > 1024 * 1024:
            return 127
        operation = read_pdf_page if sys.argv[-3] == "pdf_page" else read_docx_segment
        result = operation(data[8:], unit, offset)
    elif sys.argv[-3] == "xlsx_list":
        from minimax_code.attachment_xlsx import list_sheets

        if len(data) > 2 * 1024 * 1024:
            return 127
        result = list_sheets(data)
    elif sys.argv[-3] == "xlsx_extract":
        from minimax_code.attachment_xlsx import extract_sheet

        if len(data) < 4:
            return 127
        length = int.from_bytes(data[:4], "big")
        if not 1 <= length <= 124 or len(data) < 4 + length or len(data) - 4 - length > 2 * 1024 * 1024:
            return 127
        try:
            sheet = data[4:4 + length].decode("utf-8", errors="strict")
        except UnicodeError:
            return 127
        result = extract_sheet(data[4 + length:], sheet)
    elif len(data) > 10 * 1024 * 1024:
        return 127
    elif sys.argv[-3] == "pdf":
        from minimax_code.attachment_pdf import parse_pdf

        result = parse_pdf(data)
    elif sys.argv[-3] == "docx":
        from minimax_code.attachment_docx import parse_docx

        result = parse_docx(data)
    else:
        from minimax_code.attachment_xlsx import parse_xlsx

        result = parse_xlsx(data)
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True).encode("ascii"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
