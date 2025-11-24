"""macOS implementation using Accessibility APIs."""
# pylint: disable=import-error,logging-fstring-interpolation,broad-except,invalid-name,line-too-long
# pylint: disable=too-few-public-methods,global-statement,too-many-statements,too-many-branches
# pylint: disable=too-many-locals,too-many-instance-attributes,too-many-arguments

import ctypes
import ctypes.util
import os
import plistlib
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from janela.interfaces import Janela
from janela.interfaces.models import Monitor, Window
from janela.logger import logger


# Core Foundation / Core Graphics / Accessibility bindings are kept lightweight to avoid
# importing the heavy PyObjC umbrella frameworks at startup.
CF = None  # type: ignore
CG = None  # type: ignore
AS = None  # type: ignore

# CoreFoundation types and helpers
c_void_p = ctypes.c_void_p
c_uint32 = ctypes.c_uint32
c_int32 = ctypes.c_int32
c_bool = ctypes.c_bool
c_long = ctypes.c_long
c_longlong = ctypes.c_longlong
c_double = ctypes.c_double

kCFStringEncodingUTF8 = 0x08000100
kCFPropertyListBinaryFormat_v1_0 = 200
kCFNumberSInt64Type = 4

kAXErrorSuccess = 0
kAXValueCGPointType = 1
kAXValueCGSizeType = 2

kCGWindowListOptionAll = 0
kCGWindowListOptionOnScreenOnly = 1
kCGNullWindowID = 0


def _windows_overlap(a: Window, b: Window, threshold: float = 0.9) -> bool:
    """Return True when windows overlap almost entirely (used to drop tab/duplicate views)."""
    ax2, ay2 = a.x + a.width, a.y + a.height
    bx2, by2 = b.x + b.width, b.y + b.height
    inter_w = max(0, min(ax2, bx2) - max(a.x, b.x))
    inter_h = max(0, min(ay2, by2) - max(a.y, b.y))
    inter_area = inter_w * inter_h
    min_area = min(a.width * a.height, b.width * b.height)
    return min_area > 0 and inter_area >= min_area * threshold


def _window_summary(win: Window, members: Optional[List[str]] = None) -> str:
    names = members if members is not None else [win.name]
    names_text = ", ".join(names)
    return f"{win.id}:[{names_text}] ({win.width}x{win.height}@{win.x},{win.y})"


@dataclass
class _WindowRecord:
    window: Window
    bounds: Tuple[int, int, int, int]
    members: List[str]

# Populated at runtime by _init_mac_apis
CFRelease = None  # type: ignore
CFRetain = None  # type: ignore
CFDictionaryCreate = None  # type: ignore
CFStringCreateWithCString = None  # type: ignore
CFStringGetCString = None  # type: ignore
CFStringGetLength = None  # type: ignore
CFStringGetMaximumSizeForEncoding = None  # type: ignore
CFNumberGetValue = None  # type: ignore
CFPropertyListCreateData = None  # type: ignore
CFDataGetLength = None  # type: ignore
CFDataGetBytePtr = None  # type: ignore
CFArrayGetCount = None  # type: ignore
CFArrayGetValueAtIndex = None  # type: ignore

kCFBooleanTrue = None  # type: ignore
kCFBooleanFalse = None  # type: ignore

CGWindowListCopyWindowInfo = None  # type: ignore
CGGetActiveDisplayList = None  # type: ignore
CGDisplayBounds = None  # type: ignore
CGMainDisplayID = None  # type: ignore
CGPreflightScreenCaptureAccess = None  # type: ignore
CGRequestScreenCaptureAccess = None  # type: ignore

AXIsProcessTrustedWithOptions = None  # type: ignore
AXIsProcessTrusted = None  # type: ignore
AXUIElementCopyAttributeValue = None  # type: ignore
AXUIElementCreateApplication = None  # type: ignore
AXUIElementPerformAction = None  # type: ignore
AXUIElementSetAttributeValue = None  # type: ignore
AXValueCreate = None  # type: ignore

kAXTrustedCheckOptionPrompt = None  # type: ignore
kAXMinimizedAttribute = None  # type: ignore
kAXPositionAttribute = None  # type: ignore
kAXSizeAttribute = None  # type: ignore
kAXTitleAttribute = None  # type: ignore
kAXWindowsAttribute = None  # type: ignore
kAXRaiseAction = None  # type: ignore
kAXCloseButtonAttribute = None  # type: ignore
kAXPressAction = None  # type: ignore
kAXWindowNumberAttribute = None  # type: ignore

_cf_string_cache: Dict[str, c_void_p] = {}


class CGPoint(ctypes.Structure):
    """CGPoint struct for Core Graphics calls."""

    _fields_ = [("x", c_double), ("y", c_double)]


class CGSize(ctypes.Structure):
    """CGSize struct for Core Graphics calls."""

    _fields_ = [("width", c_double), ("height", c_double)]


class CGRect(ctypes.Structure):
    """CGRect struct for Core Graphics calls."""

    _fields_ = [("origin", CGPoint), ("size", CGSize)]


def _load_symbol(lib, name: str, restype=None, argtypes=None):
    try:
        func = getattr(lib, name)
    except AttributeError:
        return None
    if restype is not None:
        func.restype = restype
    if argtypes is not None:
        func.argtypes = argtypes
    return func


def _safe_cf_release(obj: Optional[int]) -> None:
    if obj and CFRelease:
        try:
            CFRelease(obj)
        except Exception:
            pass


def _cfstring(value: str) -> c_void_p:
    cached = _cf_string_cache.get(value)
    if cached:
        return cached
    new_value = CFStringCreateWithCString(None, value.encode("utf-8"), kCFStringEncodingUTF8)
    _cf_string_cache[value] = new_value
    return new_value


def _cfstring_to_py(cf_string: Optional[int]) -> str:
    if not cf_string:
        return ""
    length = CFStringGetLength(cf_string)
    max_size = CFStringGetMaximumSizeForEncoding(length, kCFStringEncodingUTF8) + 1
    buffer = ctypes.create_string_buffer(max_size)
    success = CFStringGetCString(cf_string, buffer, max_size, kCFStringEncodingUTF8)
    return buffer.value.decode("utf-8", errors="ignore") if success else ""


def _cfnumber_to_int(cf_number: Optional[int]) -> Optional[int]:
    if not cf_number:
        return None
    value = c_longlong()
    success = CFNumberGetValue(cf_number, kCFNumberSInt64Type, ctypes.byref(value))
    return int(value.value) if success else None


def _cfarray_to_plist(cf_array: Optional[int]):
    if not cf_array:
        return None
    data = CFPropertyListCreateData(None, cf_array, kCFPropertyListBinaryFormat_v1_0, 0, None)
    if not data:
        return None
    try:
        length = CFDataGetLength(data)
        ptr = CFDataGetBytePtr(data)
        if not ptr or length <= 0:
            return None
        raw = ctypes.string_at(ptr, length)
        return plistlib.loads(raw)
    finally:
        _safe_cf_release(data)


def _cfarray_to_ax_list(cf_array: Optional[int]) -> List[int]:
    if not cf_array:
        return []
    count = CFArrayGetCount(cf_array)
    items: List[int] = []
    for idx in range(count):
        item = CFArrayGetValueAtIndex(cf_array, idx)
        if item:
            if CFRetain:
                CFRetain(item)
            items.append(int(item))
    _safe_cf_release(cf_array)
    return items


def _init_mac_apis() -> None:
    """Bind minimal macOS APIs via ctypes for faster startup."""
    global CF, CG, AS
    global CFRelease, CFRetain, CFDictionaryCreate, CFStringCreateWithCString
    global CFStringGetCString, CFStringGetLength, CFStringGetMaximumSizeForEncoding
    global CFNumberGetValue, CFPropertyListCreateData, CFDataGetLength, CFDataGetBytePtr
    global CFArrayGetCount, CFArrayGetValueAtIndex, kCFBooleanTrue, kCFBooleanFalse
    global CGWindowListCopyWindowInfo, CGGetActiveDisplayList, CGDisplayBounds, CGMainDisplayID
    global CGPreflightScreenCaptureAccess, CGRequestScreenCaptureAccess
    global AXIsProcessTrustedWithOptions, AXIsProcessTrusted, AXUIElementCopyAttributeValue
    global AXUIElementCreateApplication, AXUIElementPerformAction, AXUIElementSetAttributeValue
    global AXValueCreate
    global kAXTrustedCheckOptionPrompt, kAXMinimizedAttribute, kAXPositionAttribute
    global kAXSizeAttribute, kAXTitleAttribute, kAXWindowsAttribute, kAXRaiseAction
    global kAXCloseButtonAttribute, kAXPressAction, kAXWindowNumberAttribute

    if CF and CG and AS:
        return

    cf_path = ctypes.util.find_library("CoreFoundation") or "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    CF = ctypes.CDLL(cf_path, use_errno=True)

    CFRelease = _load_symbol(CF, "CFRelease", restype=None, argtypes=[c_void_p])
    CFRetain = _load_symbol(CF, "CFRetain", restype=c_void_p, argtypes=[c_void_p])
    CFDictionaryCreate = _load_symbol(
        CF,
        "CFDictionaryCreate",
        restype=c_void_p,
        argtypes=[c_void_p, ctypes.POINTER(c_void_p), ctypes.POINTER(c_void_p), c_long, c_void_p, c_void_p],
    )
    CFStringCreateWithCString = _load_symbol(
        CF, "CFStringCreateWithCString", restype=c_void_p, argtypes=[c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    )
    CFStringGetCString = _load_symbol(
        CF, "CFStringGetCString", restype=c_bool, argtypes=[c_void_p, ctypes.c_char_p, c_long, ctypes.c_uint32]
    )
    CFStringGetLength = _load_symbol(CF, "CFStringGetLength", restype=c_long, argtypes=[c_void_p])
    CFStringGetMaximumSizeForEncoding = _load_symbol(
        CF, "CFStringGetMaximumSizeForEncoding", restype=c_long, argtypes=[c_long, ctypes.c_uint32]
    )
    CFNumberGetValue = _load_symbol(CF, "CFNumberGetValue", restype=c_bool, argtypes=[c_void_p, ctypes.c_int, c_void_p])
    CFPropertyListCreateData = _load_symbol(
        CF, "CFPropertyListCreateData", restype=c_void_p, argtypes=[c_void_p, c_void_p, ctypes.c_uint32, ctypes.c_uint32, c_void_p]
    )
    CFDataGetLength = _load_symbol(CF, "CFDataGetLength", restype=c_long, argtypes=[c_void_p])
    CFDataGetBytePtr = _load_symbol(CF, "CFDataGetBytePtr", restype=ctypes.POINTER(ctypes.c_ubyte), argtypes=[c_void_p])
    CFArrayGetCount = _load_symbol(CF, "CFArrayGetCount", restype=c_long, argtypes=[c_void_p])
    CFArrayGetValueAtIndex = _load_symbol(CF, "CFArrayGetValueAtIndex", restype=c_void_p, argtypes=[c_void_p, c_long])

    kCFBooleanTrue = c_void_p.in_dll(CF, "kCFBooleanTrue")
    kCFBooleanFalse = c_void_p.in_dll(CF, "kCFBooleanFalse")

    cg_path = ctypes.util.find_library("CoreGraphics") or "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    CG = ctypes.CDLL(cg_path, use_errno=True)
    CGWindowListCopyWindowInfo = _load_symbol(
        CG, "CGWindowListCopyWindowInfo", restype=c_void_p, argtypes=[c_uint32, c_uint32]
    )
    CGGetActiveDisplayList = _load_symbol(
        CG, "CGGetActiveDisplayList", restype=c_int32, argtypes=[c_uint32, ctypes.POINTER(c_uint32), ctypes.POINTER(c_uint32)]
    )
    CGDisplayBounds = _load_symbol(CG, "CGDisplayBounds", restype=CGRect, argtypes=[c_uint32])
    CGMainDisplayID = _load_symbol(CG, "CGMainDisplayID", restype=c_uint32, argtypes=None)
    CGPreflightScreenCaptureAccess = _load_symbol(CG, "CGPreflightScreenCaptureAccess", restype=c_bool, argtypes=None)
    CGRequestScreenCaptureAccess = _load_symbol(CG, "CGRequestScreenCaptureAccess", restype=c_bool, argtypes=None)

    as_path = ctypes.util.find_library("ApplicationServices") or "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    AS = ctypes.CDLL(as_path, use_errno=True)
    AXIsProcessTrustedWithOptions = _load_symbol(AS, "AXIsProcessTrustedWithOptions", restype=c_bool, argtypes=[c_void_p])
    AXIsProcessTrusted = _load_symbol(AS, "AXIsProcessTrusted", restype=c_bool, argtypes=None)
    AXUIElementCopyAttributeValue = _load_symbol(
        AS, "AXUIElementCopyAttributeValue", restype=c_int32, argtypes=[c_void_p, c_void_p, ctypes.POINTER(c_void_p)]
    )
    AXUIElementCreateApplication = _load_symbol(AS, "AXUIElementCreateApplication", restype=c_void_p, argtypes=[ctypes.c_int])
    AXUIElementPerformAction = _load_symbol(AS, "AXUIElementPerformAction", restype=c_int32, argtypes=[c_void_p, c_void_p])
    AXUIElementSetAttributeValue = _load_symbol(AS, "AXUIElementSetAttributeValue", restype=c_int32, argtypes=[c_void_p, c_void_p, c_void_p])
    AXValueCreate = _load_symbol(AS, "AXValueCreate", restype=c_void_p, argtypes=[ctypes.c_int, c_void_p])

    kAXTrustedCheckOptionPrompt = _cfstring("AXTrustedCheckOptionPrompt")
    kAXMinimizedAttribute = _cfstring("AXMinimized")
    kAXPositionAttribute = _cfstring("AXPosition")
    kAXSizeAttribute = _cfstring("AXSize")
    kAXTitleAttribute = _cfstring("AXTitle")
    kAXWindowsAttribute = _cfstring("AXWindows")
    kAXRaiseAction = _cfstring("AXRaise")
    kAXCloseButtonAttribute = _cfstring("AXCloseButton")
    kAXPressAction = _cfstring("AXPress")
    kAXWindowNumberAttribute = _cfstring("AXWindowNumber")


class MacOSImpl(Janela):  # pylint: disable=too-many-public-methods
    """macOS-specific window management implementation."""

    def __init__(self) -> None:
        _init_mac_apis()

        self._restore_bounds: Dict[str, Tuple[int, int, int, int]] = {}
        self._warned_screen_recording = False
        self._ax_missing_window_ids: set[str] = set()
        self._window_cache: List[Window] = []
        self._window_by_id: Dict[str, Window] = {}
        self._ax_windows_by_pid: Dict[int, List[int]] = {}
        self._cache_valid = False
        self._screen_recording_checked = False
        self._max_display_extent: Optional[int] = None
        self._ensure_accessibility_permissions()
        self._ensure_screen_recording_permissions()

    def _invalidate_cache(self) -> None:
        self._cache_valid = False

    def _ensure_accessibility_permissions(self) -> None:
        options = None
        if AXIsProcessTrustedWithOptions and kAXTrustedCheckOptionPrompt and CFDictionaryCreate:
            keys = (c_void_p * 1)(kAXTrustedCheckOptionPrompt)
            values = (c_void_p * 1)(kCFBooleanTrue)
            options = CFDictionaryCreate(None, keys, values, 1, None, None)

        try:
            trusted = AXIsProcessTrustedWithOptions(options) if AXIsProcessTrustedWithOptions else False
            if not trusted and AXIsProcessTrusted:
                trusted = AXIsProcessTrusted()
        finally:
            _safe_cf_release(options)

        if not trusted:
            raise PermissionError(
                "Janela requires Accessibility access. Grant permission in "
                "System Settings → Privacy & Security → Accessibility and re-run."
            )

    def _ensure_screen_recording_permissions(self) -> None:
        if self._screen_recording_checked:
            return
        self._screen_recording_checked = True

        try:
            if CGPreflightScreenCaptureAccess and CGPreflightScreenCaptureAccess():
                return
        except Exception:
            pass

        if CGRequestScreenCaptureAccess:
            try:
                if CGRequestScreenCaptureAccess():
                    return
            except Exception:
                pass

        self._open_screen_recording_settings()

    def _open_screen_recording_settings(self) -> None:
        try:
            subprocess.run(
                [
                    "open",
                    "x-apple.systempreferences:com.apple.preference.security?"
                    "Privacy_ScreenRecording",
                ],
                check=False,
            )
        except Exception:
            logger.warning(
                "Please enable Screen Recording for your terminal/Python in "
                "System Settings → Privacy & Security → Screen Recording."
            )

    def _clear_ax_windows_cache(self) -> None:
        for windows in self._ax_windows_by_pid.values():
            for win_ref in windows:
                _safe_cf_release(win_ref)
        self._ax_windows_by_pid = {}

    def _get_window_list(self, options: int) -> List[dict]:
        if not CGWindowListCopyWindowInfo:
            return []
        window_array = CGWindowListCopyWindowInfo(options, kCGNullWindowID)
        if not window_array:
            return []
        try:
            plist_obj = _cfarray_to_plist(window_array)
            if isinstance(plist_obj, list):
                return plist_obj
            return []
        finally:
            _safe_cf_release(window_array)

    def get_monitors(self) -> List[Monitor]:
        count = c_uint32(0)
        monitors: List[Monitor] = []
        if not CGGetActiveDisplayList:
            return monitors

        CGGetActiveDisplayList(0, None, ctypes.byref(count))
        if count.value == 0:
            return monitors

        display_ids = (c_uint32 * count.value)()
        CGGetActiveDisplayList(count.value, display_ids, ctypes.byref(count))

        max_extent = 0
        for display_id in display_ids[: count.value]:
            if not CGDisplayBounds:
                continue
            bounds = CGDisplayBounds(display_id)
            max_extent = max(max_extent, int(bounds.origin.y + bounds.size.height))
            monitor = Monitor(
                wm=self,
                id=int(display_id),
                name=f"Display {display_id}",
                width=int(bounds.size.width),
                height=int(bounds.size.height),
                x=int(bounds.origin.x),
                y=int(bounds.origin.y),
            )
            monitors.append(monitor)
        if max_extent > 0:
            self._max_display_extent = max_extent
        return monitors

    def _active_window_from_list(self, window_list: List[dict]) -> str:
        for win in window_list:
            if win.get("kCGWindowLayer", 0) == 0:
                return str(win.get("kCGWindowNumber", "")) or ""
        return ""

    def get_active_window_id(self) -> str:
        window_list = self._get_window_list(kCGWindowListOptionOnScreenOnly)
        return self._active_window_from_list(window_list)

    def _copy_attribute(self, element: c_void_p, attribute: c_void_p) -> Tuple[int, Optional[int]]:
        if not element:
            return -1, None
        value = c_void_p()
        err = AXUIElementCopyAttributeValue(element, attribute, ctypes.byref(value))
        return err, value.value

    def _get_ax_windows_for_pid(self, pid: int) -> List[int]:
        if pid in self._ax_windows_by_pid:
            return self._ax_windows_by_pid[pid]

        app_ref = AXUIElementCreateApplication(pid) if AXUIElementCreateApplication else None
        if not app_ref:
            self._ax_windows_by_pid[pid] = []
            return []
        try:
            err, ax_windows = self._copy_attribute(app_ref, kAXWindowsAttribute)
            if err == kAXErrorSuccess and ax_windows:
                windows = _cfarray_to_ax_list(ax_windows)
                self._ax_windows_by_pid[pid] = windows
                return windows
        finally:
            _safe_cf_release(app_ref)

        self._ax_windows_by_pid[pid] = []
        return []

    def _get_ax_window_numbers_for_pid(self, pid: int) -> set[int]:
        """Return window numbers exposed via AX for a process."""
        numbers: set[int] = set()
        for ax_ref in self._get_ax_windows_for_pid(pid):
            err, win_id_ref = self._copy_attribute(ax_ref, kAXWindowNumberAttribute)
            try:
                if err == kAXErrorSuccess and win_id_ref:
                    win_id = _cfnumber_to_int(win_id_ref)
                    if win_id is not None:
                        numbers.add(win_id)
            finally:
                _safe_cf_release(win_id_ref)
        return numbers

    def _find_ax_window(self, window: Window, log_missing: bool = True):
        if window.pid is None:
            return None

        ax_windows = self._get_ax_windows_for_pid(window.pid)
        if not ax_windows:
            if log_missing and window.id not in self._ax_missing_window_ids:
                logger.warning(f"Unable to fetch AX windows for pid {window.pid}")
                self._ax_missing_window_ids.add(window.id)
            return None

        target_id = int(window.id)
        for ax_ref in ax_windows:
            err, win_id_ref = self._copy_attribute(ax_ref, kAXWindowNumberAttribute)
            try:
                if err == kAXErrorSuccess and win_id_ref:
                    win_id = _cfnumber_to_int(win_id_ref)
                    if win_id is not None and win_id == target_id:
                        return c_void_p(ax_ref)
            finally:
                _safe_cf_release(win_id_ref)

        for ax_ref in ax_windows:
            err, title_ref = self._copy_attribute(ax_ref, kAXTitleAttribute)
            try:
                if err == kAXErrorSuccess and title_ref:
                    title_lower = _cfstring_to_py(title_ref).lower()
                    target_lower = (window.name or "").lower()
                    if (
                        title_lower == target_lower
                        or target_lower in title_lower
                        or title_lower in target_lower
                    ):
                        return c_void_p(ax_ref)
            finally:
                _safe_cf_release(title_ref)

        if ax_windows:
            return c_void_p(ax_windows[0])
        return None

    def _set_window_position(self, ax_window: c_void_p, x: int, y: int) -> bool:
        point = CGPoint(x, y)
        pos_value = AXValueCreate(kAXValueCGPointType, ctypes.byref(point)) if AXValueCreate else None
        if not pos_value:
            logger.error("Accessibility CGPoint type missing; cannot move window")
            return False
        try:
            err = AXUIElementSetAttributeValue(ax_window, kAXPositionAttribute, pos_value)
            return err == kAXErrorSuccess
        finally:
            _safe_cf_release(pos_value)

    def _set_window_size(self, ax_window: c_void_p, width: int, height: int) -> bool:
        size = CGSize(width, height)
        size_value = AXValueCreate(kAXValueCGSizeType, ctypes.byref(size)) if AXValueCreate else None
        if not size_value:
            logger.error("Accessibility CGSize type missing; cannot resize window")
            return False
        try:
            err = AXUIElementSetAttributeValue(ax_window, kAXSizeAttribute, size_value)
            return err == kAXErrorSuccess
        finally:
            _safe_cf_release(size_value)

    def _ax_position_for(self, _window: Window, x: int, y: int, _height: Optional[int] = None) -> Tuple[int, int]:
        """macOS AX appears to accept CG-style bottom-left coords; keep identity."""
        return x, y

    def _clamp_to_monitor(
        self,
        window: Window,
        x: int,
        y: int,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> Tuple[int, int]:
        monitor = self.get_monitor_for_window(window)
        if not monitor:
            return x, y
        w = width if width is not None else window.width
        h = height if height is not None else window.height
        min_x = monitor.x
        min_y = monitor.y
        max_x = monitor.x + monitor.width - w
        max_y = monitor.y + monitor.height - h
        clamped_x = min(max(x, min_x), max_x)
        clamped_y = min(max(y, min_y), max_y)
        return clamped_x, clamped_y

    def _get_pid_bounds(self, pid: int) -> Dict[str, Tuple[int, int, int, int]]:
        """Return current CG bounds for a PID keyed by window id."""
        result: Dict[str, Tuple[int, int, int, int]] = {}
        for win in self._get_window_list(kCGWindowListOptionAll):
            if win.get("kCGWindowOwnerPID") != pid:
                continue
            wid = win.get("kCGWindowNumber")
            bounds = win.get("kCGWindowBounds", {}) or {}
            if wid is None:
                continue
            result[str(wid)] = (
                int(bounds.get("X", 0)),
                int(bounds.get("Y", 0)),
                int(bounds.get("Width", 0)),
                int(bounds.get("Height", 0)),
            )
        return result

    @staticmethod
    def _merge_members(target: _WindowRecord, names: List[str]) -> None:
        target.members = list(dict.fromkeys(target.members + names))

    def _probe_coupled_records(
        self,
        pid: int,
        primary: _WindowRecord,
        others: List[_WindowRecord],
    ) -> set[str]:
        """Move the primary window slightly; any siblings that move with it are merged."""
        if not others:
            return set()
        primary_pos = primary.bounds
        ax_primary = self._find_ax_window(primary.window, log_missing=False)
        if not ax_primary:
            return set()

        dx, dy = 80, 60
        target_x = primary_pos[0] + dx
        target_y = primary_pos[1] + dy
        target_x, target_y = self._clamp_to_monitor(primary.window, target_x, target_y, primary.bounds[2], primary.bounds[3])
        ax_x, ax_y = self._ax_position_for(primary.window, target_x, target_y, primary.bounds[3])
        if not self._set_window_position(ax_primary, ax_x, ax_y):
            logger.debug("Coupling probe: move failed for pid %s window %s", pid, primary.window.name)
            return set()

        time.sleep(0.1)
        moved_bounds = self._get_pid_bounds(pid)

        moved_primary = moved_bounds.get(primary.window.id)
        if not moved_primary:
            logger.debug("Coupling probe: primary bounds missing after move for pid %s", pid)
            self._set_window_position(ax_primary, primary_pos[0], primary_pos[1])
            return set()
        delta_primary = (moved_primary[0] - primary_pos[0], moved_primary[1] - primary_pos[1])

        coupled: set[str] = set()
        tol = 10
        for rec in others:
            before = rec.bounds
            after = moved_bounds.get(rec.window.id)
            if not after:
                continue
            delta = (after[0] - before[0], after[1] - before[1])
            if abs(delta[0] - delta_primary[0]) <= tol and abs(delta[1] - delta_primary[1]) <= tol:
                coupled.add(rec.window.id)
                self._merge_members(primary, rec.members)

        orig_x, orig_y = self._clamp_to_monitor(primary.window, primary_pos[0], primary_pos[1], primary.bounds[2], primary.bounds[3])
        orig_ax_x, orig_ax_y = self._ax_position_for(primary.window, orig_x, orig_y, primary.bounds[3])
        self._set_window_position(ax_primary, orig_ax_x, orig_ax_y)
        return coupled

    def _ensure_display_extent(self) -> int:
        if self._max_display_extent is None:
            self.get_monitors()
        return self._max_display_extent or 0

    def _process_pid_records(self, pid: int, records: List[_WindowRecord]) -> List[_WindowRecord]:
        if len(records) == 1:
            return records

        max_area = max(r.bounds[2] * r.bounds[3] for r in records)
        primary = max(records, key=lambda r: r.bounds[2] * r.bounds[3])

        filtered: List[_WindowRecord] = []
        dropped_small: List[_WindowRecord] = []
        for rec in records:
            area = rec.bounds[2] * rec.bounds[3]
            if max_area > 0 and area < max_area * 0.6 and rec.bounds[3] < 150:
                dropped_small.append(rec)
                self._merge_members(primary, rec.members)
                continue
            filtered.append(rec)
        if not filtered:
            filtered = [primary]

        filtered = sorted(
            filtered,
            key=lambda r: (
                0 if r.window.is_active else 1,
                -(r.bounds[2] * r.bounds[3]),
            ),
        )

        kept: List[_WindowRecord] = []
        dropped_overlap: List[_WindowRecord] = []
        for rec in filtered:
            overlap_target = None
            for k in kept:
                if _windows_overlap(rec.window, k.window):
                    overlap_target = k
                    break
            if overlap_target:
                dropped_overlap.append(rec)
                self._merge_members(overlap_target, rec.members)
                continue
            kept.append(rec)

        if len(kept) > 1:
            primary_rec = kept[0]
            others = kept[1:]
            coupled_ids = self._probe_coupled_records(pid, primary_rec, others)
            if coupled_ids:
                kept = [r for r in kept if r.window.id not in coupled_ids]

        if len(kept) > 1:
            proc_names = {getattr(r.window, "process_name", "").lower() for r in kept}
            if len(proc_names) == 1 and next(iter(proc_names)) == "terminal":
                primary_rec = kept[0]
                for rec in kept[1:]:
                    self._merge_members(primary_rec, rec.members)
                kept = [primary_rec]

        if dropped_small or dropped_overlap:
            logger.info(
                "PID %d dedupe: kept %d; dropped %d small, %d overlap. Kept windows: %s",
                pid,
                len(kept),
                len(dropped_small),
                len(dropped_overlap),
                "; ".join(_window_summary(r.window, r.members) for r in kept),
            )

        for rec in kept:
            rec.members = list(dict.fromkeys(rec.members))
        return kept

    def list_windows(self) -> List[Window]:
        self._window_cache = []
        self._window_by_id = {}
        self._clear_ax_windows_cache()

        window_list = self._get_window_list(kCGWindowListOptionAll)
        if not window_list and not self._warned_screen_recording:
            logger.warning(
                "No windows found. macOS may require Screen Recording permission "
                "for this terminal/app to enumerate other apps' windows. "
                "Enable it in System Settings → Privacy & Security → Screen Recording."
            )
            self._open_screen_recording_settings()
            self._warned_screen_recording = True

        # Precompute AX-backed window numbers per PID to drop non-window CG entries (e.g., tabs).
        ax_window_numbers_by_pid: Dict[int, set[int]] = {}
        pids = {int(w.get("kCGWindowOwnerPID")) for w in window_list if w.get("kCGWindowOwnerPID") is not None}
        for pid in pids:
            numbers = self._get_ax_window_numbers_for_pid(pid)
            if numbers:
                ax_window_numbers_by_pid[pid] = numbers

        active_id = self._active_window_from_list(window_list)
        records_by_pid: Dict[int, List[_WindowRecord]] = {}
        no_pid_records: List[_WindowRecord] = []
        for win in window_list:
            if win.get("kCGWindowLayer", 0) != 0:
                continue

            owner_name = win.get("kCGWindowOwnerName", "")
            window_name = win.get("kCGWindowName", "")
            if not owner_name or not window_name:
                continue

            bounds = win.get("kCGWindowBounds", {}) or {}
            window_number = win.get("kCGWindowNumber")
            pid_val = win.get("kCGWindowOwnerPID")
            if window_number is None or pid_val is None:
                continue
            pid_int = int(pid_val)
            allowed_numbers = ax_window_numbers_by_pid.get(pid_int)
            if allowed_numbers is not None and int(window_number) not in allowed_numbers:
                continue

            window_id = str(win.get("kCGWindowNumber"))
            pid = win.get("kCGWindowOwnerPID")
            if window_id in self._window_by_id:
                continue
            x_val = int(bounds.get("X", 0))
            y_val = int(bounds.get("Y", 0))
            w_val = int(bounds.get("Width", 0))
            h_val = int(bounds.get("Height", 0))
            window_obj = Window(
                wm=self,
                id=window_id,
                name=window_name or owner_name,
                x=x_val,
                y=y_val,
                width=w_val,
                height=h_val,
                is_active=window_id == active_id,
                pid=int(pid) if pid is not None else None,
            )
            setattr(window_obj, "process_name", owner_name)
            record = _WindowRecord(window=window_obj, bounds=(x_val, y_val, w_val, h_val), members=[window_obj.name])
            if window_obj.pid is None:
                no_pid_records.append(record)
            else:
                records_by_pid.setdefault(window_obj.pid, []).append(record)
            self._window_by_id[window_id] = window_obj

        grouped_records: List[_WindowRecord] = list(no_pid_records)
        for pid, records in records_by_pid.items():
            grouped_records.extend(self._process_pid_records(pid, records))

        self._window_cache = [rec.window for rec in grouped_records]
        for rec in grouped_records:
            deduped_names = list(dict.fromkeys(rec.members))
            setattr(rec.window, "group_members", deduped_names)

        if (
            self._window_cache
            and all(w.pid == os.getpid() for w in self._window_cache)
            and not self._warned_screen_recording
        ):
            logger.warning(
                "Only this Python process is visible. macOS often restricts window "
                "enumeration without Screen Recording permission. Enable it in "
                "System Settings → Privacy & Security → Screen Recording."
            )
            self._open_screen_recording_settings()
            self._warned_screen_recording = True

        self._cache_valid = True
        return list(self._window_cache)

    def _update_cached_window(
        self, window: Window, position: Optional[Tuple[int, int]] = None, size: Optional[Tuple[int, int]] = None
    ) -> None:
        cached = self._window_by_id.get(window.id)
        if not cached:
            return
        if position is not None:
            x_val, y_val = position
            cached.x = x_val
            cached.y = y_val
            window.x = x_val
            window.y = y_val
        if size is not None:
            width_val, height_val = size
            cached.width = width_val
            cached.height = height_val
            window.width = width_val
            window.height = height_val

    def get_monitor_for_window(self, window: Window) -> Optional[Monitor]:
        monitors = self.get_monitors()
        for monitor in monitors:
            if (
                monitor.x <= window.x < monitor.x + monitor.width
                and monitor.y <= window.y < monitor.y + monitor.height
            ):
                return monitor
        return None

    def move_window_to_position(self, window: Window, x: int, y: int):
        ax_window = self._find_ax_window(window)
        if not ax_window:
            logger.error(f"Could not find AX window for '{window.name}'")
            return
        x, y = self._clamp_to_monitor(window, x, y)
        ax_x, ax_y = self._ax_position_for(window, x, y)
        if self._set_window_position(ax_window, ax_x, ax_y):
            self._update_cached_window(window, position=(x, y))
            self._invalidate_cache()
        else:
            logger.error(f"Failed to move window '{window.name}' to ({x}, {y})")

    def resize_window(self, window: Window, width: int, height: int):
        ax_window = self._find_ax_window(window)
        if not ax_window:
            logger.error(f"Could not find AX window for '{window.name}'")
            return
        if self._set_window_size(ax_window, width, height):
            self._update_cached_window(window, size=(width, height))
            self._invalidate_cache()
        else:
            logger.error(f"Failed to resize window '{window.name}' to ({width}, {height})")

    def minimize_window(self, window: Window):
        ax_window = self._find_ax_window(window)
        if not ax_window:
            logger.error(f"Could not find AX window for '{window.name}'")
            return
        err = AXUIElementSetAttributeValue(ax_window, kAXMinimizedAttribute, kCFBooleanTrue)
        if err != kAXErrorSuccess:
            logger.error(f"Failed to minimize window '{window.name}'")
        else:
            self._invalidate_cache()

    def maximize_window(self, window: Window):
        monitor = self.get_monitor_for_window(window)
        if monitor:
            self._restore_bounds[window.id] = (
                window.x,
                window.y,
                window.width,
                window.height,
            )
            self.resize_window(window, monitor.width, monitor.height)
            self.move_window_to_position(window, monitor.x, monitor.y)

    def move_to_monitor(self, window: Window, monitor: Monitor):
        target_x = monitor.x + (monitor.width - window.width) // 2
        target_y = monitor.y + (monitor.height - window.height) // 2
        self.move_window_to_position(window, target_x, target_y)

    def verify_window_move(
        self, window: Window, target_monitor: Monitor, expected_x: int, expected_y: int
    ) -> bool:
        updated_window = self.get_window_by_id(window.id)
        if updated_window is None:
            return False
        return (
            updated_window.x == expected_x
            and updated_window.y == expected_y
            and self.get_monitor_for_window(updated_window) == target_monitor
        )

    def get_window_by_id(self, window_id: str) -> Optional[Window]:
        if not self._cache_valid:
            self.list_windows()
        cached = self._window_by_id.get(window_id)
        if cached:
            return cached
        return None

    def focus_window(self, window: Window):
        ax_window = self._find_ax_window(window)
        if not ax_window:
            logger.error(f"Could not find AX window for '{window.name}'")
            return
        err = AXUIElementPerformAction(ax_window, kAXRaiseAction)
        if err == kAXErrorSuccess:
            window.is_active = True
        else:
            logger.error(f"Failed to focus window '{window.name}'")

    def close_window(self, window: Window):
        ax_window = self._find_ax_window(window)
        if not ax_window:
            logger.error(f"Could not find AX window for '{window.name}'")
            return
        err, close_button = self._copy_attribute(ax_window, kAXCloseButtonAttribute)
        try:
            if err == kAXErrorSuccess and close_button:
                press_err = AXUIElementPerformAction(close_button, kAXPressAction)
                if press_err != kAXErrorSuccess:
                    logger.error(f"Failed to close window '{window.name}' via close button")
            else:
                logger.error(f"No close button available for '{window.name}'")
            self._invalidate_cache()
        finally:
            _safe_cf_release(close_button)

    def list_monitors(self) -> List[Monitor]:
        return sorted(self.get_monitors(), key=lambda m: m.name)

    def get_active_window(self) -> Optional[Window]:
        active_window_id = self.get_active_window_id()
        return self.get_window_by_id(active_window_id)

    def verify_window_positions(self) -> bool:
        return True

    def get_window_by_name(self, name: str) -> Optional[Window]:
        window_list = self.list_windows()
        for window in window_list:
            if window.name == name:
                return window
        return None

    def get_monitor_by_id(self, monitor_id: int) -> Optional[Monitor]:
        monitors = self.get_monitors()
        for monitor in monitors:
            if monitor.id == monitor_id:
                return monitor
        return None

    def is_window_maximized(self, window: Window) -> bool:
        monitor = self.get_monitor_for_window(window)
        if monitor:
            return (
                window.x == monitor.x
                and window.y == monitor.y
                and window.width == monitor.width
                and window.height == monitor.height
            )
        return False

    def unmaximize_window(self, window: Window) -> None:
        restore = self._restore_bounds.get(window.id)
        if restore:
            x, y, width, height = restore
            self.move_window_to_position(window, x, y)
            self.resize_window(window, width, height)
            return
        self.resize_window(window, max(800, window.width // 2), max(600, window.height // 2))

    def can_control_window(self, window: Window) -> bool:
        return self._find_ax_window(window, log_missing=False) is not None
