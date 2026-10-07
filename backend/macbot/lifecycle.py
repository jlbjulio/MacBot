import os
import time


def parent_signal():
    """Wait for a parent pipe without holding Windows CRT locks during DLL imports."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
        kernel.PeekNamedPipe.restype = wintypes.BOOL
        handle = kernel.GetStdHandle(-10 & 0xFFFFFFFF)
        while True:
            available = wintypes.DWORD()
            if not kernel.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
                return
            if available.value:
                return
            time.sleep(0.2)
    else:
        import selectors
        import sys
        with selectors.DefaultSelector() as selector:
            selector.register(sys.stdin, selectors.EVENT_READ)
            while not selector.select(timeout=0.2):
                pass
