"""macOS implementation using Accessibility APIs."""
# pylint: disable=import-error,logging-fstring-interpolation,broad-except,invalid-name,line-too-long
# pylint: disable=too-few-public-methods,global-statement,too-many-statements,too-many-branches
# pylint: disable=too-many-locals,too-many-instance-attributes,too-many-arguments
# pylint: disable=too-many-lines

import ctypes
import ctypes.util
import os
import plistlib
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from janela.interfaces import Janela
from janela.interfaces.models import Monitor, Window
from janela.logger import logger
from janela.util.restore import fallback_restore_bounds


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
# Optional opt-in to legacy coupling probe that nudges windows to detect duplicates.
_ENABLE_COUPLED_PROBE = os.getenv("JANELA_MAC_COUPLED_PROBE", "").lower() in {"1", "true", "yes", "on"}


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
CGDisplayCopyDisplayMode = None  # type: ignore
CGDisplayModeGetWidth = None  # type: ignore
CGDisplayModeGetHeight = None  # type: ignore
CGDisplayModeGetPixelWidth = None  # type: ignore
CGDisplayModeGetPixelHeight = None  # type: ignore
CGDisplayModeRelease = None  # type: ignore

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
    value = c_longlong(0)
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
    global CGDisplayCopyDisplayMode, CGDisplayModeGetWidth, CGDisplayModeGetHeight
    global CGDisplayModeGetPixelWidth, CGDisplayModeGetPixelHeight, CGDisplayModeRelease
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
    CGDisplayCopyDisplayMode = _load_symbol(CG, "CGDisplayCopyDisplayMode", restype=c_void_p, argtypes=[c_uint32])
    CGDisplayModeGetWidth = _load_symbol(CG, "CGDisplayModeGetWidth", restype=c_int32, argtypes=[c_void_p])
    CGDisplayModeGetHeight = _load_symbol(CG, "CGDisplayModeGetHeight", restype=c_int32, argtypes=[c_void_p])
    CGDisplayModeGetPixelWidth = _load_symbol(CG, "CGDisplayModeGetPixelWidth", restype=c_int32, argtypes=[c_void_p])
    CGDisplayModeGetPixelHeight = _load_symbol(CG, "CGDisplayModeGetPixelHeight", restype=c_int32, argtypes=[c_void_p])
    CGDisplayModeRelease = _load_symbol(CG, "CGDisplayModeRelease", restype=None, argtypes=[c_void_p])
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
        logger.debug("Initializing macOS implementation")
        _init_mac_apis()

        self._api_lock = threading.RLock()
        self._restore_bounds: Dict[str, Tuple[int, int, int, int]] = {}
        self._warned_screen_recording = False
        self._ax_missing_window_ids: set[str] = set()
        self._window_cache: List[Window] = []
        self._window_by_id: Dict[str, Window] = {}
        self._ax_windows_by_pid: Dict[int, List[int]] = {}
        self._cache_valid = False
        self._screen_recording_checked = False
        self._max_display_extent: Optional[int] = None
        # Track per-display backing scale (device pixels per point) for coordinate conversions.
        self._display_scales: Dict[int, float] = {}
        logger.debug("Starting state: caches empty, restore bounds cleared")
        self._ensure_accessibility_permissions()
        self._ensure_screen_recording_permissions()

    def _invalidate_cache(self) -> None:
        logger.debug(
            "Invalidating window cache (cached=%d, window_by_id=%d)",
            len(self._window_cache),
            len(self._window_by_id),
        )
        self._cache_valid = False

    def _ensure_accessibility_permissions(self) -> None:
        logger.info("Ensuring Accessibility permissions are granted")
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
        logger.info("Accessibility permissions verified")

    def _ensure_screen_recording_permissions(self) -> None:
        logger.info("Ensuring Screen Recording permissions are granted")
        if self._screen_recording_checked:
            logger.debug("Screen recording permissions already checked")
            return
        self._screen_recording_checked = True

        try:
            if CGPreflightScreenCaptureAccess and CGPreflightScreenCaptureAccess():
                logger.debug("Preflight screen capture access succeeded")
                return
        except Exception:
            pass

        if CGRequestScreenCaptureAccess:
            try:
                if CGRequestScreenCaptureAccess():
                    logger.debug("Screen capture access granted via prompt")
                    return
            except Exception:
                pass

        self._open_screen_recording_settings()

    def _open_screen_recording_settings(self) -> None:
        logger.info("Opening Screen Recording settings for user action")
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
        total_cached = sum(len(windows) for windows in self._ax_windows_by_pid.values())
        logger.debug(
            "Clearing AX window cache for %d pids (%d windows)",
            len(self._ax_windows_by_pid),
            total_cached,
        )
        for windows in self._ax_windows_by_pid.values():
            for win_ref in windows:
                _safe_cf_release(win_ref)
        self._ax_windows_by_pid = {}

    def _is_cg_window_visible(self, cg_win: dict, window_id: str) -> bool:
        bounds = cg_win.get("kCGWindowBounds", {}) or {}
        width = int(bounds.get("Width", 0))
        height = int(bounds.get("Height", 0))
        is_onscreen = bool(cg_win.get("kCGWindowIsOnscreen", 0))
        alpha = float(cg_win.get("kCGWindowAlpha", 1.0))
        if not is_onscreen:
            logger.debug("Skipping window %s: not on screen", window_id)
            return False
        if width <= 1 or height <= 1:
            logger.debug("Skipping window %s: tiny bounds (%d x %d)", window_id, width, height)
            return False
        if alpha <= 0:
            logger.debug("Skipping window %s: fully transparent (alpha=%s)", window_id, alpha)
            return False
        return True

    def _is_ax_window_minimized(self, ax_window: Optional[c_void_p]) -> Optional[bool]:
        if not ax_window:
            return None
        err, minimized_ref = self._copy_attribute(ax_window, kAXMinimizedAttribute)
        try:
            if err != kAXErrorSuccess or minimized_ref is None:
                logger.debug("AX minimized state unavailable (err=%s)", err)
                return None
            return minimized_ref == kCFBooleanTrue
        finally:
            _safe_cf_release(minimized_ref)

    def _is_window_visible(self, cg_win: dict, window_obj: Window, ax_window: Optional[c_void_p]) -> bool:
        if not self._is_cg_window_visible(cg_win, window_obj.id):
            return False
        minimized = self._is_ax_window_minimized(ax_window)
        if minimized:
            logger.debug("Skipping window '%s' (%s) because it is minimized", window_obj.name, window_obj.id)
            return False
        return True

    def _get_window_list(self, options: int) -> List[dict]:
        logger.debug("Fetching window list with options=%d", options)
        if not CGWindowListCopyWindowInfo:
            return []
        window_array = CGWindowListCopyWindowInfo(options, kCGNullWindowID)
        if not window_array:
            return []
        try:
            plist_obj = _cfarray_to_plist(window_array)
            if isinstance(plist_obj, list):
                logger.debug("Fetched %d raw windows from CGWindowListCopyWindowInfo", len(plist_obj))
                return plist_obj
            return []
        finally:
            _safe_cf_release(window_array)

    def get_monitors(self) -> List[Monitor]:
        logger.info("Enumerating monitors")
        with self._api_lock:
            count = c_uint32(0)
            monitors: List[Monitor] = []
            self._display_scales = {}
            if not CGGetActiveDisplayList:
                logger.debug("CoreGraphics display list function unavailable")
                return monitors

            CGGetActiveDisplayList(0, None, ctypes.byref(count))
            if count.value == 0:
                logger.debug("No monitors reported by CoreGraphics")
                return monitors

            display_ids = (c_uint32 * count.value)()
            CGGetActiveDisplayList(count.value, display_ids, ctypes.byref(count))

            max_extent = 0
            for display_id in display_ids[: count.value]:
                if not CGDisplayBounds:
                    continue
                bounds = CGDisplayBounds(display_id)
                max_extent = max(max_extent, int(bounds.origin.y + bounds.size.height))
                scale = 1.0
                if CGDisplayCopyDisplayMode:
                    mode = CGDisplayCopyDisplayMode(display_id)
                    if mode:
                        try:
                            mode_width = CGDisplayModeGetWidth(mode) if CGDisplayModeGetWidth else 0
                            mode_height = CGDisplayModeGetHeight(mode) if CGDisplayModeGetHeight else 0
                            pixel_width = CGDisplayModeGetPixelWidth(mode) if CGDisplayModeGetPixelWidth else mode_width
                            pixel_height = CGDisplayModeGetPixelHeight(mode) if CGDisplayModeGetPixelHeight else mode_height
                            if mode_width:
                                scale = max(scale, pixel_width / mode_width)
                            if mode_height:
                                scale = max(scale, pixel_height / mode_height, scale)
                        finally:
                            if CGDisplayModeRelease:
                                CGDisplayModeRelease(mode)
                self._display_scales[int(display_id)] = scale or 1.0
                logger.debug(
                    "Display %s scale set to %.2f (raw bounds: x=%d y=%d w=%d h=%d)",
                    display_id,
                    self._display_scales[int(display_id)],
                    int(bounds.origin.x),
                    int(bounds.origin.y),
                    int(bounds.size.width),
                    int(bounds.size.height),
                )
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
            logger.info(
                "Detected %d monitor(s); max display extent=%s",
                len(monitors),
                self._max_display_extent,
            )
            return monitors

    def _active_window_from_list(self, window_list: List[dict]) -> str:
        for win in window_list:
            if win.get("kCGWindowLayer", 0) == 0:
                active_id = str(win.get("kCGWindowNumber", "")) or ""
                logger.debug("Active window candidate from list: %s", active_id)
                return active_id
        return ""

    def get_active_window_id(self) -> str:
        with self._api_lock:
            window_list = self._get_window_list(kCGWindowListOptionOnScreenOnly)
            active_id = self._active_window_from_list(window_list)
            logger.info("Active window id resolved to: %s", active_id)
            return active_id

    def _copy_attribute(self, element: c_void_p, attribute: c_void_p) -> Tuple[int, Optional[int]]:
        if not element:
            return -1, None
        value = c_void_p()
        err = AXUIElementCopyAttributeValue(element, attribute, ctypes.byref(value))
        return err, value.value

    def _get_ax_windows_for_pid(self, pid: int) -> List[int]:
        if pid in self._ax_windows_by_pid:
            logger.debug("AX windows for pid %d loaded from cache (%d entries)", pid, len(self._ax_windows_by_pid[pid]))
            return self._ax_windows_by_pid[pid]

        logger.debug("Fetching AX windows for pid %d", pid)
        app_ref = AXUIElementCreateApplication(pid) if AXUIElementCreateApplication else None
        if not app_ref:
            logger.debug("AX application reference missing for pid %d", pid)
            self._ax_windows_by_pid[pid] = []
            return []
        try:
            err, ax_windows = self._copy_attribute(app_ref, kAXWindowsAttribute)
            if err == kAXErrorSuccess and ax_windows:
                windows = _cfarray_to_ax_list(ax_windows)
                self._ax_windows_by_pid[pid] = windows
                logger.debug("Fetched %d AX windows for pid %d", len(windows), pid)
                return windows
        finally:
            _safe_cf_release(app_ref)

        self._ax_windows_by_pid[pid] = []
        logger.debug("No AX windows available for pid %d", pid)
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
        logger.debug("AX window numbers for pid %d: %s", pid, sorted(numbers))
        return numbers

    def _find_ax_window(self, window: Window, log_missing: bool = True):
        logger.debug("Locating AX window for id=%s name='%s' pid=%s", window.id, window.name, window.pid)
        if window.pid is None:
            logger.debug("Window %s has no pid; skipping AX lookup", window.id)
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
                        logger.debug("Matched AX window %s by window number", window.id)
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
                        logger.debug("Matched AX window %s by title '%s'", window.id, title_lower)
                        return c_void_p(ax_ref)
            finally:
                _safe_cf_release(title_ref)

        if ax_windows:
            logger.debug("Falling back to first AX window for pid %s", window.pid)
            return c_void_p(ax_windows[0])
        logger.debug("No AX match found for window %s", window.id)
        return None

    def _set_window_position(self, ax_window: c_void_p, x: int, y: int) -> bool:
        logger.info("Setting window position to (%d, %d)", x, y)
        point = CGPoint(x, y)
        pos_value = AXValueCreate(kAXValueCGPointType, ctypes.byref(point)) if AXValueCreate else None
        if not pos_value:
            logger.error("Accessibility CGPoint type missing; cannot move window")
            return False
        try:
            err = AXUIElementSetAttributeValue(ax_window, kAXPositionAttribute, pos_value)
            if err != kAXErrorSuccess:
                logger.debug("AXUIElementSetAttributeValue returned error code %s when moving", err)
            return err == kAXErrorSuccess
        finally:
            _safe_cf_release(pos_value)

    def _set_window_size(self, ax_window: c_void_p, width: int, height: int) -> bool:
        logger.info("Setting window size to (%d, %d)", width, height)
        size = CGSize(width, height)
        size_value = AXValueCreate(kAXValueCGSizeType, ctypes.byref(size)) if AXValueCreate else None
        if not size_value:
            logger.error("Accessibility CGSize type missing; cannot resize window")
            return False
        try:
            err = AXUIElementSetAttributeValue(ax_window, kAXSizeAttribute, size_value)
            if err != kAXErrorSuccess:
                logger.debug("AXUIElementSetAttributeValue returned error code %s when resizing", err)
            return err == kAXErrorSuccess
        finally:
            _safe_cf_release(size_value)

    def _monitor_scale(self, monitor: Optional[Monitor]) -> float:
        """
        Return the backing scale factor (device pixels per point) for a monitor.

        Defaults to 1.0 when unknown to avoid altering coordinates on non-HiDPI displays.
        """
        if monitor is None:
            return 1.0
        scale = self._display_scales.get(monitor.id, 1.0)
        return scale if scale > 0 else 1.0

    def _ax_position_for(self, window: Window, x: int, y: int, height: Optional[int] = None) -> Tuple[int, int]:
        """
        Convert CG window coordinates (device pixels) to AX coordinates (points).
        """
        monitor = self.get_monitor_for_window(window)
        rect_height = height if height is not None else window.height
        scale = self._monitor_scale(monitor)
        ax_x = int(round(x / scale))
        ax_y = int(round(y / scale))
        logger.debug(
            "Mapping CG coords (%d, %d, h=%d) to AX coords (%d, %d) using monitor %s with scale %.2f",
            x,
            y,
            rect_height,
            ax_x,
            ax_y,
            getattr(monitor, "id", None),
            scale,
        )
        return ax_x, ax_y

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
        logger.debug("PID %d bounds snapshot contains %d window(s)", pid, len(result))
        return result

    @staticmethod
    def _merge_members(target: _WindowRecord, names: List[str]) -> None:
        logger.debug("Merging window members %s into %s", names, target.members)
        target.members = list(dict.fromkeys(target.members + names))

    def _probe_coupled_records(
        self,
        pid: int,
        primary: _WindowRecord,
        others: List[_WindowRecord],
    ) -> set[str]:
        """Move the primary window slightly; any siblings that move with it are merged."""
        if not others:
            logger.debug("No sibling windows to probe for pid %d", pid)
            return set()
        logger.debug(
            "Probing for coupled windows for pid %d using primary %s and %d other(s)",
            pid,
            primary.window.id,
            len(others),
        )
        primary_pos = primary.bounds
        ax_primary = self._find_ax_window(primary.window, log_missing=False)
        if not ax_primary:
            return set()

        dx, dy = 80, 60
        target_x = primary_pos[0] + dx
        target_y = primary_pos[1] + dy
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

        orig_ax_x, orig_ax_y = self._ax_position_for(primary.window, primary_pos[0], primary_pos[1], primary.bounds[3])
        self._set_window_position(ax_primary, orig_ax_x, orig_ax_y)
        logger.debug("Coupling probe complete for pid %d; coupled windows: %s", pid, sorted(coupled))
        return coupled

    def _ensure_display_extent(self) -> int:
        if self._max_display_extent is None:
            logger.debug("Max display extent unknown; refreshing monitors")
            self.get_monitors()
        logger.debug("Current max display extent: %s", self._max_display_extent)
        return self._max_display_extent or 0

    @staticmethod
    def _should_merge_records(a: _WindowRecord, b: _WindowRecord) -> bool:
        """
        Decide whether two records likely represent the same window.

        Requirements:
        - Same process name (avoid cross-app merges).
        - Titles match (case-insensitive).
        - Significant overlap (>=90% of smaller area via _windows_overlap).
        - Similar size (within 8% per dimension).
        - Centers within a small pixel tolerance relative to window size.
        """
        proc_a = getattr(a.window, "process_name", "").lower()
        proc_b = getattr(b.window, "process_name", "").lower()
        names_match = (a.window.name or "").lower() == (b.window.name or "").lower()
        overlap = _windows_overlap(a.window, b.window)

        w_a, h_a = a.bounds[2], a.bounds[3]
        w_b, h_b = b.bounds[2], b.bounds[3]
        size_tol = 0.08
        size_close = (
            abs(w_a - w_b) <= max(w_a, w_b) * size_tol
            and abs(h_a - h_b) <= max(h_a, h_b) * size_tol
        )

        x_a, y_a = a.bounds[0], a.bounds[1]
        x_b, y_b = b.bounds[0], b.bounds[1]
        cx_a, cy_a = x_a + w_a / 2, y_a + h_a / 2
        cx_b, cy_b = x_b + w_b / 2, y_b + h_b / 2
        pos_tol = max(min(w_a, h_a, w_b, h_b) * 0.08, 12)
        pos_close = abs(cx_a - cx_b) <= pos_tol and abs(cy_a - cy_b) <= pos_tol

        return proc_a == proc_b and names_match and overlap and size_close and pos_close

    def _process_pid_records(self, pid: int, records: List[_WindowRecord]) -> List[_WindowRecord]:
        logger.debug(
            "Processing %d window record(s) for pid %d", len(records), pid
        )
        if len(records) == 1:
            logger.debug("Single window for pid %d; skipping dedupe", pid)
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

        # Collapse exact duplicates (same name and identical bounds) without moving windows.
        deduped: Dict[Tuple[str, Tuple[int, int, int, int]], _WindowRecord] = {}
        for rec in filtered:
            proc = getattr(rec.window, "process_name", "").lower()
            sig = (proc, rec.window.name.lower(), rec.bounds)
            existing = deduped.get(sig)
            if existing:
                self._merge_members(existing, rec.members)
                continue
            deduped[sig] = rec
        filtered = list(deduped.values())

        kept: List[_WindowRecord] = []
        dropped_overlap: List[_WindowRecord] = []
        for rec in filtered:
            overlap_target = None
            for k in kept:
                if self._should_merge_records(rec, k):
                    overlap_target = k
                    break
            if overlap_target:
                dropped_overlap.append(rec)
                self._merge_members(overlap_target, rec.members)
                continue
            kept.append(rec)

        if len(kept) > 1 and _ENABLE_COUPLED_PROBE:
            primary_rec = kept[0]
            others = kept[1:]
            coupled_ids = self._probe_coupled_records(pid, primary_rec, others)
            if coupled_ids:
                kept = [r for r in kept if r.window.id not in coupled_ids]

        logger.debug(
            "After processing pid %d records: kept %d, dropped_small=%d, dropped_overlap=%d",
            pid,
            len(kept),
            len(dropped_small),
            len(dropped_overlap),
        )
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
        with self._api_lock:
            return self._list_windows_unlocked()

    def _list_windows_unlocked(self) -> List[Window]:
        logger.info(
            "Listing windows (cache_valid=%s, cached=%d)",
            self._cache_valid,
            len(self._window_cache),
        )
        self._window_cache = []
        self._window_by_id = {}
        self._clear_ax_windows_cache()

        window_list = self._get_window_list(kCGWindowListOptionOnScreenOnly)
        logger.debug("CG returned %d window entries (onscreen only)", len(window_list))
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
        logger.debug(
            "AX window number map prepared for %d pid(s)", len(ax_window_numbers_by_pid)
        )

        active_id = self._active_window_from_list(window_list)
        records_by_pid: Dict[int, List[_WindowRecord]] = {}
        no_pid_records: List[_WindowRecord] = []
        dropped_uncontrollable = 0
        dropped_invisible = 0
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
            ax_window = self._find_ax_window(window_obj, log_missing=False)
            if not ax_window:
                dropped_uncontrollable += 1
                logger.debug(
                    "Skipping window '%s' (%s) pid=%s due to missing AX control",
                    window_obj.name,
                    window_id,
                    window_obj.pid,
                )
                continue
            if not self._is_window_visible(win, window_obj, ax_window):
                dropped_invisible += 1
                continue
            setattr(window_obj, "process_name", owner_name)
            record = _WindowRecord(window=window_obj, bounds=(x_val, y_val, w_val, h_val), members=[window_obj.name])
            if window_obj.pid is None:
                no_pid_records.append(record)
            else:
                records_by_pid.setdefault(window_obj.pid, []).append(record)
            self._window_by_id[window_id] = window_obj
            self._record_normal_bounds(window_obj, ax_window)
        logger.debug(
            "Window records grouped: %d with pid, %d without pid",
            len(records_by_pid),
            len(no_pid_records),
        )

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
        logger.info(
            "Window listing complete: %d window(s) cached; %d dropped (no AX control); %d dropped (invisible/minimized)",
            len(self._window_cache),
            dropped_uncontrollable,
            dropped_invisible,
        )
        return list(self._window_cache)

    def _update_cached_window(
        self, window: Window, position: Optional[Tuple[int, int]] = None, size: Optional[Tuple[int, int]] = None
    ) -> None:
        cached = self._window_by_id.get(window.id)
        if not cached:
            logger.debug("No cached window entry to update for id=%s", window.id)
            return
        logger.debug(
            "Updating cached window %s (position=%s, size=%s)",
            window.id,
            position,
            size,
        )
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

    def _record_normal_bounds(self, window: Window, ax_window: Optional[c_void_p] = None) -> None:
        """
        Remember the last seen non-minimized, non-maximized bounds for a window.

        This enables restores/unminimize even if the user triggered maximize/minimize
        manually outside Janela.
        """
        try:
            minimized = (
                self._is_ax_window_minimized(ax_window)
                if ax_window is not None
                else self.is_window_minimized(window)
            )
            if minimized:
                return
            if self.is_window_maximized(window):
                return
            self._restore_bounds[window.id] = (window.x, window.y, window.width, window.height)
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug("Unable to record bounds for window %s: %s", window.id, exc)

    def get_monitor_for_window(self, window: Window) -> Optional[Monitor]:
        logger.debug(
            "Resolving monitor for window %s at (%d, %d) size (%d x %d)",
            window.id,
            window.x,
            window.y,
            window.width,
            window.height,
        )
        monitors = self.get_monitors()
        best_monitor: Optional[Monitor] = None
        best_area = 0
        wx1, wy1 = window.x, window.y
        wx2, wy2 = window.x + window.width, window.y + window.height

        for monitor in monitors:
            mx1, my1 = monitor.x, monitor.y
            mx2, my2 = monitor.x + monitor.width, monitor.y + monitor.height
            inter_w = max(0, min(wx2, mx2) - max(wx1, mx1))
            inter_h = max(0, min(wy2, my2) - max(wy1, my1))
            area = inter_w * inter_h
            if area > best_area:
                best_area = area
                best_monitor = monitor
            if monitor.contains(window.x, window.y) and best_monitor is None:
                best_monitor = monitor

        if best_monitor:
            logger.debug(
                "Window %s assigned to monitor %s with overlap area %d",
                window.id,
                best_monitor.id,
                best_area,
            )
        else:
            logger.debug("No monitor contains window %s", window.id)
        return best_monitor

    def move_window_to_position(self, window: Window, x: int, y: int):
        logger.info("Request to move window '%s' (%s) to (%d, %d)", window.name, window.id, x, y)
        with self._api_lock:
            ax_window = self._find_ax_window(window)
            if not ax_window:
                logger.error(f"Could not find AX window for '{window.name}'")
                return
            ax_x, ax_y = self._ax_position_for(window, x, y)
            if self._set_window_position(ax_window, ax_x, ax_y):
                self._update_cached_window(window, position=(x, y))
                self._record_normal_bounds(window, ax_window)
                self._invalidate_cache()
                logger.info("Moved window '%s' (%s) to (%d, %d)", window.name, window.id, x, y)
            else:
                logger.error(f"Failed to move window '{window.name}' to ({x}, {y})")

    def resize_window(self, window: Window, width: int, height: int):
        logger.info("Request to resize window '%s' (%s) to (%d, %d)", window.name, window.id, width, height)
        with self._api_lock:
            ax_window = self._find_ax_window(window)
            if not ax_window:
                logger.error(f"Could not find AX window for '{window.name}'")
                return
            monitor = self.get_monitor_for_window(window)
            scale = self._monitor_scale(monitor)
            target_width = int(round(width / scale))
            target_height = int(round(height / scale))
            if self._set_window_size(ax_window, target_width, target_height):
                self._update_cached_window(window, size=(width, height))
                self._record_normal_bounds(window, ax_window)
                self._invalidate_cache()
                logger.info(
                    "Resized window '%s' (%s) to (%d, %d) (AX size %d x %d, scale %.2f)",
                    window.name,
                    window.id,
                    width,
                    height,
                    target_width,
                    target_height,
                    scale,
                )
            else:
                logger.error(f"Failed to resize window '{window.name}' to ({width}, {height})")

    def minimize_window(self, window: Window):
        logger.info("Request to minimize window '%s' (%s)", window.name, window.id)
        with self._api_lock:
            self._record_normal_bounds(window)
            ax_window = self._find_ax_window(window)
            if not ax_window:
                logger.error(f"Could not find AX window for '{window.name}'")
                return
            err = AXUIElementSetAttributeValue(ax_window, kAXMinimizedAttribute, kCFBooleanTrue)
            if err != kAXErrorSuccess:
                logger.error(f"Failed to minimize window '{window.name}'")
            else:
                logger.info("Minimized window '%s' (%s)", window.name, window.id)
                self._invalidate_cache()

    def maximize_window(self, window: Window):
        logger.info("Request to maximize window '%s' (%s)", window.name, window.id)
        with self._api_lock:
            monitor = self.get_monitor_for_window(window)
            if monitor:
                self._restore_bounds[window.id] = (
                    window.x,
                    window.y,
                    window.width,
                    window.height,
                )
                logger.debug(
                    "Stored restore bounds for window %s: %s",
                    window.id,
                    self._restore_bounds[window.id],
                )
                self.resize_window(window, monitor.width, monitor.height)
                self.move_window_to_position(window, monitor.x, monitor.y)
                logger.info("Maximized window '%s' on monitor %s", window.name, monitor.id)

    def move_to_monitor(self, window: Window, monitor: Monitor):
        logger.info("Moving window '%s' (%s) to monitor %s", window.name, window.id, monitor.id)
        with self._api_lock:
            target_x = monitor.x + (monitor.width - window.width) // 2
            target_y = monitor.y + (monitor.height - window.height) // 2
            self.move_window_to_position(window, target_x, target_y)

    def verify_window_move(
        self, window: Window, target_monitor: Monitor, expected_x: int, expected_y: int
    ) -> bool:
        updated_window = self.get_window_by_id(window.id)
        if updated_window is None:
            logger.debug("Window %s missing when verifying move", window.id)
            return False
        current_monitor = self.get_monitor_for_window(updated_window)
        matches = (
            updated_window.x == expected_x
            and updated_window.y == expected_y
            and current_monitor == target_monitor
        )
        logger.debug(
            "Verify move for window %s: expected (%d, %d) on monitor %s, actual (%d, %d) on %s, matches=%s",
            window.id,
            expected_x,
            expected_y,
            target_monitor.id,
            updated_window.x,
            updated_window.y,
            getattr(current_monitor, "id", None),
            matches,
        )
        return matches

    def get_window_by_id(self, window_id: str) -> Optional[Window]:
        logger.debug("Fetching window by id=%s (cache_valid=%s)", window_id, self._cache_valid)
        with self._api_lock:
            if not self._cache_valid:
                self.list_windows()
            cached = self._window_by_id.get(window_id)
            if cached:
                logger.debug("Found window %s in cache", window_id)
                return cached
            logger.debug("Window %s not found in cache", window_id)
            return None

    def focus_window(self, window: Window):
        logger.info("Request to focus window '%s' (%s)", window.name, window.id)
        with self._api_lock:
            ax_window = self._find_ax_window(window)
            if not ax_window:
                logger.error(f"Could not find AX window for '{window.name}'")
                return
            err = AXUIElementPerformAction(ax_window, kAXRaiseAction)
            if err == kAXErrorSuccess:
                window.is_active = True
                logger.info("Focused window '%s' (%s)", window.name, window.id)
            else:
                logger.error(f"Failed to focus window '{window.name}'")

    def close_window(self, window: Window):
        logger.info("Request to close window '%s' (%s)", window.name, window.id)
        with self._api_lock:
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
                logger.info("Closed window '%s' (%s)", window.name, window.id)
            finally:
                _safe_cf_release(close_button)

    def list_monitors(self) -> List[Monitor]:
        monitors = sorted(self.get_monitors(), key=lambda m: m.name)
        logger.info("Listing monitors: %s", [m.id for m in monitors])
        return monitors

    def get_active_window(self) -> Optional[Window]:
        with self._api_lock:
            active_window_id = self.get_active_window_id()
            active_window = self.get_window_by_id(active_window_id)
            logger.info("Active window resolved to %s", getattr(active_window, "id", None))
            return active_window

    def verify_window_positions(self) -> bool:
        logger.debug("Verifying window positions (macOS stub returns True)")
        return True

    def get_window_by_name(self, name: str) -> Optional[Window]:
        logger.debug("Searching for window by exact name '%s'", name)
        with self._api_lock:
            window_list = self.list_windows()
            for window in window_list:
                if window.name == name:
                    logger.debug("Found window by name '%s' with id %s", name, window.id)
                    return window
            logger.debug("No window found with name '%s'", name)
            return None

    def get_monitor_by_id(self, monitor_id: int) -> Optional[Monitor]:
        logger.debug("Searching for monitor by id %s", monitor_id)
        with self._api_lock:
            monitors = self.get_monitors()
            for monitor in monitors:
                if monitor.id == monitor_id:
                    logger.debug("Found monitor %s", monitor.id)
                    return monitor
            logger.debug("Monitor %s not found", monitor_id)
            return None

    def is_window_minimized(self, window: Window) -> bool:
        with self._api_lock:
            ax_window = self._find_ax_window(window)
            return self._is_ax_window_minimized(ax_window) is True

    def is_window_maximized(self, window: Window) -> bool:
        with self._api_lock:
            monitor = self.get_monitor_for_window(window)
            if monitor:
                maximized = (
                    window.x == monitor.x
                    and window.y == monitor.y
                    and window.width == monitor.width
                    and window.height == monitor.height
                )
                logger.debug("Window %s maximized=%s on monitor %s", window.id, maximized, monitor.id)
                return maximized
            logger.debug("Cannot determine maximized state for window %s without monitor", window.id)
            return False

    def unminimize_window(self, window: Window) -> None:  # pylint: disable=duplicate-code
        logger.info("Request to unminimize window '%s' (%s)", window.name, window.id)
        with self._api_lock:
            ax_window = self._find_ax_window(window)
            if not ax_window:
                logger.error(f"Could not find AX window for '{window.name}'")
                return
            err = AXUIElementSetAttributeValue(ax_window, kAXMinimizedAttribute, kCFBooleanFalse)
            if err != kAXErrorSuccess:
                logger.error("Failed to unminimize window '%s'", window.name)
                return

            restore = self._restore_bounds.get(window.id)
            if restore:
                x, y, width, height = restore
                self.move_window_to_position(window, x, y)
                self.resize_window(window, width, height)
            else:
                monitor = self.get_monitor_for_window(window)
                monitors = self.get_monitors()
                x, y, width, height = fallback_restore_bounds(window, monitor, monitors)
                if monitors:
                    self.resize_window(window, width, height)
                    self.move_window_to_position(window, x, y)
                else:
                    logger.debug("No monitor available for fallback restore of %s", window.id)
            self._invalidate_cache()
            self._record_normal_bounds(window, ax_window)

    def unmaximize_window(self, window: Window) -> None:
        with self._api_lock:
            restore = self._restore_bounds.get(window.id)
            if restore:
                x, y, width, height = restore
                logger.info("Restoring window '%s' (%s) to saved bounds %s", window.name, window.id, restore)
                self.move_window_to_position(window, x, y)
                self.resize_window(window, width, height)
                self._record_normal_bounds(window)
                return
            logger.info("No restore bounds for window '%s' (%s); resizing to half dimensions", window.name, window.id)
            monitors = self.get_monitors()
            monitor = self.get_monitor_for_window(window)
            x, y, width, height = fallback_restore_bounds(window, monitor, monitors)
            if monitors:
                self.resize_window(window, width, height)
                self.move_window_to_position(window, x, y)
            self._record_normal_bounds(window)

    def can_control_window(self, window: Window) -> bool:
        with self._api_lock:
            can_control = self._find_ax_window(window, log_missing=False) is not None
            logger.debug("Control check for window '%s' (%s): %s", window.name, window.id, can_control)
            return can_control
