"""Connected Kindle device detection, MTP access, and book/clipping enumeration.

MTP backend strategy
--------------------
Standard CLI tools (mtp-files, mtp-getfile …) each open and close the USB
device in a separate process.  Every open triggers ``libusb_detach_kernel_driver``
which ejects the device from the macOS file system; the subsequent
``libusb_attach_kernel_driver`` on close does not reliably re-mount it.

This module uses **ctypes to call libmtp directly** so the device is opened
once, all operations run inside that single session, and the device is closed
exactly once.  This is the same architectural choice MacDroid makes.
"""

import contextlib
import ctypes
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterator, Optional, List


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EBOOK_EXTS      = {".kfx", ".azw3", ".azw", ".mobi", ".prc", ".epub"}
_ANNOTATION_EXTS = {".yjr", ".yjf", ".mbp"}

# LIBMTP_FILETYPE_FOLDER is the first value in the enum → 0
_LIBMTP_FILETYPE_FOLDER = 0

# LIBMTP error codes (LIBMTP_error_number_t)
_LIBMTP_ERROR_CODES = {
    0: "NONE",
    1: "GENERAL",
    2: "PTP_LAYER",
    3: "USB_LAYER",
    4: "MEMORY_ALLOCATION",
    5: "NO_DEVICE_ATTACHED",
    6: "STORAGE_FULL",
    7: "CONNECTING",
    8: "CANCELLED",
}


# ---------------------------------------------------------------------------
# ctypes struct definitions (must match libmtp.h exactly for 64-bit macOS)
# ---------------------------------------------------------------------------

class _DeviceEntry(ctypes.Structure):
    """LIBMTP_device_entry_struct"""
    _fields_ = [
        ("vendor",       ctypes.c_char_p),   # offset  0 (8 bytes)
        ("vendor_id",    ctypes.c_uint16),   # offset  8
        ("product",      ctypes.c_char_p),   # offset 16 (compiler pads to 8-byte align)
        ("product_id",   ctypes.c_uint16),   # offset 24
        ("device_flags", ctypes.c_uint32),   # offset 28
    ]


class _RawDevice(ctypes.Structure):
    """LIBMTP_raw_device_struct"""
    _fields_ = [
        ("device_entry", _DeviceEntry),      # offset  0 (32 bytes)
        ("bus_location", ctypes.c_uint32),   # offset 32
        ("devnum",       ctypes.c_uint8),    # offset 36
    ]


class _File(ctypes.Structure):
    """LIBMTP_file_struct (linked list node)"""

_File._fields_ = [
    ("item_id",          ctypes.c_uint32),         # offset  0
    ("parent_id",        ctypes.c_uint32),         # offset  4
    ("storage_id",       ctypes.c_uint32),         # offset  8
    # compiler inserts 4-byte pad here to align char*
    ("filename",         ctypes.c_char_p),         # offset 16
    ("filesize",         ctypes.c_uint64),         # offset 24
    ("modificationdate", ctypes.c_int64),          # offset 32  (time_t = int64 on macOS 64-bit)
    ("filetype",         ctypes.c_int),            # offset 40  (enum = int32)
    # compiler inserts 4-byte pad here to align pointer
    ("next",             ctypes.POINTER(_File)),   # offset 48
]


class _Folder(ctypes.Structure):
    """LIBMTP_folder_struct (tree node)"""

_Folder._fields_ = [
    ("folder_id",  ctypes.c_uint32),              # offset  0
    ("parent_id",  ctypes.c_uint32),              # offset  4
    ("storage_id", ctypes.c_uint32),              # offset  8
    # compiler inserts 4-byte pad here to align char*
    ("name",       ctypes.c_char_p),              # offset 16
    ("sibling",    ctypes.POINTER(_Folder)),      # offset 24
    ("child",      ctypes.POINTER(_Folder)),      # offset 32
]


# ---------------------------------------------------------------------------
# libmtp library loader
# ---------------------------------------------------------------------------

_LIBMTP_SEARCH_PATHS = [
    "/usr/local/lib/libmtp.dylib",                                    # Homebrew (Intel)
    "/opt/homebrew/lib/libmtp.dylib",                                 # Homebrew (Apple Silicon)
    "/Applications/MacDroid.app/Contents/Frameworks/libmtp.dylib",   # MacDroid bundle
    "/usr/lib/libmtp.so.9",                                           # Linux
    "/usr/lib/x86_64-linux-gnu/libmtp.so.9",                         # Debian/Ubuntu x86_64
    "/usr/lib/aarch64-linux-gnu/libmtp.so.9",                        # Debian/Ubuntu arm64
]


def _load_libmtp() -> Optional[ctypes.CDLL]:
    """Load the libmtp shared library and configure essential function signatures.
    Returns the loaded CDLL, or None if not found.
    """
    lib = None
    for path in _LIBMTP_SEARCH_PATHS:
        if Path(path).exists():
            try:
                lib = ctypes.CDLL(path)
                break
            except OSError:
                continue

    if lib is None:
        return None

    # --- LIBMTP_Init ---
    lib.LIBMTP_Init.restype  = None
    lib.LIBMTP_Init.argtypes = []

    # --- LIBMTP_Detect_Raw_Devices ---
    lib.LIBMTP_Detect_Raw_Devices.restype  = ctypes.c_int
    lib.LIBMTP_Detect_Raw_Devices.argtypes = [
        ctypes.POINTER(ctypes.POINTER(_RawDevice)),
        ctypes.POINTER(ctypes.c_int),
    ]

    # --- LIBMTP_Open_Raw_Device_Uncached ---
    lib.LIBMTP_Open_Raw_Device_Uncached.restype  = ctypes.c_void_p
    lib.LIBMTP_Open_Raw_Device_Uncached.argtypes = [ctypes.POINTER(_RawDevice)]

    # --- LIBMTP_Release_Device ---
    lib.LIBMTP_Release_Device.restype  = None
    lib.LIBMTP_Release_Device.argtypes = [ctypes.c_void_p]

    # --- LIBMTP_Get_Filelisting_With_Callback ---
    lib.LIBMTP_Get_Filelisting_With_Callback.restype  = ctypes.POINTER(_File)
    lib.LIBMTP_Get_Filelisting_With_Callback.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,   # progress callback (NULL)
        ctypes.c_void_p,   # user data (NULL)
    ]

    # --- LIBMTP_destroy_file_t ---
    lib.LIBMTP_destroy_file_t.restype  = None
    lib.LIBMTP_destroy_file_t.argtypes = [ctypes.POINTER(_File)]

    # --- LIBMTP_Get_File_To_File ---
    lib.LIBMTP_Get_File_To_File.restype  = ctypes.c_int
    lib.LIBMTP_Get_File_To_File.argtypes = [
        ctypes.c_void_p,   # device
        ctypes.c_uint32,   # file id
        ctypes.c_char_p,   # destination path
        ctypes.c_void_p,   # progress callback (NULL)
        ctypes.c_void_p,   # user data (NULL)
    ]

    # --- LIBMTP_Clear_Errorstack (suppress error output) ---
    try:
        lib.LIBMTP_Clear_Errorstack.restype  = None
        lib.LIBMTP_Clear_Errorstack.argtypes = [ctypes.c_void_p]
    except AttributeError:
        pass

    return lib


# ---------------------------------------------------------------------------
# MTPDirectSession — single open/close USB cycle
# ---------------------------------------------------------------------------

class MTPDirectSession:
    """Holds an open libmtp device handle for the duration of the session.

    Opens the device exactly once; all file-listing and download operations
    run through this single handle.  Closing (via ``close()`` or the
    ``mtp_direct_session()`` context manager) releases the device exactly
    once.  This avoids the repeated detach/attach cycle that causes macOS
    to eject the Kindle.
    """

    def __init__(self, lib: ctypes.CDLL, device_handle: int,
                 folders: dict[int, str], files: List[dict]) -> None:
        self._lib    = lib
        self._dev    = device_handle   # opaque c_void_p value
        self.folders = folders         # {folder_id: folder_name}
        self.files   = files           # list of {id, name, parent_id, filetype}
        self.tmpdir  = Path(tempfile.mkdtemp(prefix="kindle_mtp_"))

    # -- lifecycle --

    def close(self) -> None:
        # Do NOT call LIBMTP_Release_Device here.
        # Calibre's pattern: the device handle is kept alive until the user
        # explicitly ejects.  Calling Release_Device triggers libusb's
        # attach_kernel_driver path, which makes macOS try to remount the
        # device as mass-storage and show an "ejected" notification.
        # For a read-only CLI session we just null the handle and let the OS
        # reclaim the USB interface when the process exits (no remount attempt).
        self._dev = None
        import shutil as _sh
        _sh.rmtree(self.tmpdir, ignore_errors=True)

    # -- helpers --

    def _parent_sdr(self, parent_id: int) -> Optional[str]:
        """Walk the folder map upward to find the closest .sdr ancestor name."""
        seen: set[int] = set()
        fid = parent_id
        while fid and fid not in seen:
            seen.add(fid)
            name = self.folders.get(fid, "")
            if name.lower().endswith(".sdr"):
                return name
            # Walk up: find the folder entry whose folder_id == fid to get its parent_id
            # (We stored only id→name; rebuild a id→parent map lazily isn't worth it here.
            # Instead, check file entries that are directories.)
            break   # flat search only — .sdr folders are direct children of documents/
        return None

    def _sdr_name_for(self, parent_id: int) -> Optional[str]:
        """Return the name of the .sdr folder that is the parent of parent_id,
        or parent_id itself if it is a .sdr folder."""
        name = self.folders.get(parent_id, "")
        if name.lower().endswith(".sdr"):
            return name
        return None

    # -- public API --

    def list_books(self) -> List[dict]:
        """Return sorted book list derived from the device folder tree."""
        books: dict[str, dict] = {}
        for name in self.folders.values():
            if name.lower().endswith(".sdr"):
                title = name[:-4]
                books.setdefault(title, {
                    "title": title, "has_ebook": False, "has_sidecar": True,
                    "clipping_files": [], "ebook_path": None, "sdr_path": None,
                })
        for f in self.files:
            if Path(f["name"]).suffix.lower() in _EBOOK_EXTS:
                title = Path(f["name"]).stem
                entry = books.setdefault(title, {
                    "title": title, "has_ebook": False, "has_sidecar": False,
                    "clipping_files": [], "ebook_path": None, "sdr_path": None,
                })
                entry["has_ebook"] = True
        return sorted(books.values(), key=lambda b: b["title"].lower())

    def _download(self, file_id: int, dest: Path) -> bool:
        ret = self._lib.LIBMTP_Get_File_To_File(
            self._dev, file_id, str(dest).encode(), None, None
        )
        # Clear error stack so libmtp doesn't print to stderr on next call
        try:
            self._lib.LIBMTP_Clear_Errorstack(self._dev)
        except Exception:
            pass
        return ret == 0 and dest.exists()

    def fetch_clippings_txt(self) -> Optional[Path]:
        """Download My Clippings.txt and return its local path."""
        for f in self.files:
            if f["name"].lower() == "my clippings.txt":
                dest = self.tmpdir / "My Clippings.txt"
                print("  Downloading My Clippings.txt …", file=sys.stderr)
                if self._download(f["id"], dest):
                    return dest
        return None

    def fetch_all_annotations(self) -> Path:
        """Download every .yjr/.yjf/.mbp file, preserving <Book.sdr>/<file> layout.

        Returns ``self.tmpdir`` — pass directly to ``scan_path()``.
        """
        targets = [
            f for f in self.files
            if Path(f["name"]).suffix.lower() in _ANNOTATION_EXTS
        ]
        if not targets:
            print("  [warn] No annotation files found on device.", file=sys.stderr)
            return self.tmpdir

        downloaded = 0
        for f in targets:
            sdr = self._sdr_name_for(f["parent_id"])
            if not sdr:
                continue   # file not inside a .sdr folder — skip

            dest_dir = self.tmpdir / sdr
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f["name"]
            print(f"  Downloading {sdr}/{f['name']} …", file=sys.stderr)
            if self._download(f["id"], dest):
                downloaded += 1
            else:
                print(f"  [warn] Failed: {f['name']}", file=sys.stderr)

        print(f"  Downloaded {downloaded}/{len(targets)} annotation file(s).",
              file=sys.stderr)
        return self.tmpdir


# ---------------------------------------------------------------------------
# Context manager for a direct libmtp session
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def mtp_direct_session() -> Iterator[MTPDirectSession]:
    """Open a single-session libmtp connection and yield an MTPDirectSession.

    The device is opened exactly once and released exactly once, avoiding
    the repeated detach/attach cycle that causes macOS to eject the Kindle.

    Raises ``RuntimeError`` if libmtp cannot be loaded or no device is found.
    """
    lib = _load_libmtp()
    if lib is None:
        raise RuntimeError(
            "libmtp not found. Install it:  brew install libmtp  (macOS) "
            "or  sudo apt install libmtp-dev  (Linux)"
        )

    lib.LIBMTP_Init()

    raw_devs_ptr  = ctypes.POINTER(_RawDevice)()
    num_raw_devs  = ctypes.c_int(0)
    err = lib.LIBMTP_Detect_Raw_Devices(
        ctypes.byref(raw_devs_ptr), ctypes.byref(num_raw_devs)
    )
    if err != 0 or num_raw_devs.value == 0:
        err_name = _LIBMTP_ERROR_CODES.get(err, f"UNKNOWN({err})")
        hints = {
            3: ("USB permission denied. "
                "The USB interface may be claimed by the macOS mass-storage driver. "
                "Try: (1) disconnect and reconnect the Kindle without unlocking it, "
                "so macOS does not auto-mount it as a disk; "
                "(2) run with sudo; "
                "(3) use --device if the Kindle appears in Finder."),
            5: ("No MTP device detected. "
                "Make sure the Kindle is connected, unlocked, "
                "USB cable supports data transfer, "
                "and set USB mode to 'File Transfer / MTP' if prompted."),
        }
        hint = hints.get(err, "Make sure Kindle is connected and unlocked.")
        raise RuntimeError(
            f"No Kindle device found via MTP "
            f"(LIBMTP error {err}: {err_name}). {hint}"
        )

    print(f"  Found {num_raw_devs.value} MTP device(s). Opening …", file=sys.stderr)

    # Calibre pattern: copy the raw device struct locally, then free the array.
    # Rationale from Calibre's libmtp.c:
    #   "We have to build and search the rawdevice list instead of creating a
    #    rawdevice directly as otherwise, dynamic bug flag assignment in libmtp
    #    does not work."
    # LIBMTP_Detect_Raw_Devices sets device-specific bug-fix flags based on the
    # vendor/product ID.  Passing a copy (not the original array pointer) to
    # LIBMTP_Open_Raw_Device_Uncached preserves those flags while allowing us to
    # free the malloc'd array immediately, just as Calibre does.
    rdev_copy = _RawDevice()
    ctypes.memmove(ctypes.byref(rdev_copy), raw_devs_ptr, ctypes.sizeof(_RawDevice))
    try:
        ctypes.CDLL(None).free(raw_devs_ptr)
    except Exception:
        pass  # memory leak if this fails, but not fatal

    # If the device is not in libmtp's database its device_flags will be 0,
    # meaning libmtp will attempt GetObjectPropList which many Kindle models
    # do not support.  All known Amazon Kindle entries in libmtp's
    # device-versions.h carry DEVICE_FLAG_BROKEN_MTPGETOBJECTPROPLIST_ALL
    # (0x1), which makes libmtp fall back to the universally supported
    # GetObjectInfo path.  Apply it here whenever the vendor is Amazon and
    # no flags were set by the database lookup.
    _AMAZON_VID = 0x1949
    _FLAG_BROKEN_PROPLIST_ALL = 0x00000001
    if (rdev_copy.device_entry.vendor_id == _AMAZON_VID
            and rdev_copy.device_entry.device_flags == 0):
        rdev_copy.device_entry.device_flags = _FLAG_BROKEN_PROPLIST_ALL
        print("  Applied BROKEN_MTPGETOBJECTPROPLIST_ALL flag for unregistered "
              "Amazon device.", file=sys.stderr)

    dev_handle = lib.LIBMTP_Open_Raw_Device_Uncached(ctypes.byref(rdev_copy))
    if not dev_handle:
        raise RuntimeError("Could not open MTP device.")

    # ---- Single call: get ALL objects (files + folders) --------------------
    # LIBMTP_Get_Filelisting_With_Callback returns every object on the device.
    # Items with filetype == 0 (LIBMTP_FILETYPE_FOLDER) are directories.
    # Using one call avoids the LIBMTP_Get_Folder_List bug on some devices.
    print("  Reading device file listing …", file=sys.stderr)
    folders: dict[int, str] = {}   # folder_id → folder_name
    files:   List[dict]     = []

    raw_ptr = lib.LIBMTP_Get_Filelisting_With_Callback(dev_handle, None, None)
    cur = raw_ptr
    while cur:
        node = cur.contents
        name = node.filename.decode(errors="replace") if node.filename else ""
        entry = {
            "id":        node.item_id,
            "name":      name,
            "parent_id": node.parent_id,
            "filetype":  node.filetype,
        }
        if node.filetype == _LIBMTP_FILETYPE_FOLDER:
            folders[node.item_id] = name
        else:
            files.append(entry)
        cur = node.next

    # Free the linked list
    cur = raw_ptr
    while cur:
        nxt = cur.contents.next
        lib.LIBMTP_destroy_file_t(cur)
        cur = nxt

    print(f"  Found {len(folders)} folders, {len(files)} files.", file=sys.stderr)
    session = MTPDirectSession(lib, dev_handle, folders, files)
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Auto-mount detection (OS mounts Kindle automatically as a disk volume)
# ---------------------------------------------------------------------------

def _is_kindle_volume(vol: Path) -> bool:
    """Return True if *vol* looks like a mounted Kindle device.

    Checks two independent signals so that NAS shares, external drives, and
    other volumes with a ``documents/`` folder are not mistakenly identified:
      1. The volume name contains "kindle" (case-insensitive), OR
      2. The volume has BOTH ``documents/`` and ``system/`` subdirectories
         (the ``system/`` directory is Kindle-specific and absent on most
         generic storage volumes).
    """
    if not vol.is_dir():
        return False
    docs = vol / "documents"
    if not docs.is_dir():
        return False
    name_lower = vol.name.lower()
    if "kindle" in name_lower:
        return True
    # Fallback: presence of system/ alongside documents/ is Kindle-specific
    return (vol / "system").is_dir()


def find_kindle() -> Optional[Path]:
    """Return the root path of a Kindle already mounted by the OS, or None.

    Scans all mounted volumes/drives rather than checking a fixed path, so
    it finds the Kindle even when macOS names it ``Kindle 1``, ``KINDLE``, etc.
    """
    # macOS: scan every volume under /Volumes/
    volumes = Path("/Volumes")
    if volumes.exists():
        try:
            for vol in sorted(volumes.iterdir()):
                if _is_kindle_volume(vol):
                    return vol
        except (PermissionError, OSError):
            pass

    # Linux: scan /media/$USER/* and /run/media/$USER/*
    for base in (Path("/media"), Path("/run/media")):
        if not base.exists():
            continue
        try:
            for user_dir in base.iterdir():
                if not user_dir.is_dir():
                    continue
                for vol in sorted(user_dir.iterdir()):
                    if _is_kindle_volume(vol):
                        return vol
        except (PermissionError, OSError):
            pass

    return None


# ---------------------------------------------------------------------------
# FUSE mount backend (Linux — jmtpfs / go-mtpfs)
# ---------------------------------------------------------------------------

def _find_fuse_mtp_tool() -> Optional[str]:
    for tool in ("jmtpfs", "go-mtpfs", "aft-mtp-mount"):
        if shutil.which(tool):
            return tool
    return None


def _fuse_unmount(mount_point: Path) -> None:
    try:
        if platform.system() == "Linux":
            subprocess.run(["fusermount", "-u", str(mount_point)],
                           capture_output=True, timeout=10)
        else:
            subprocess.run(["umount", str(mount_point)],
                           capture_output=True, timeout=10)
    except Exception:
        pass


@contextlib.contextmanager
def mtp_mount(mount_point: Optional[Path] = None) -> Iterator[Path]:
    """FUSE-based MTP mount (Linux only: jmtpfs / go-mtpfs).
    Yields the mount-point Path; unmounts on exit.
    Raises RuntimeError if no FUSE MTP tool is available.
    """
    tool = _find_fuse_mtp_tool()
    if not tool:
        raise RuntimeError(
            "No FUSE MTP tool found. "
            "Linux: sudo apt install jmtpfs"
        )

    tmp_dir: Optional[str] = None
    if mount_point is None:
        tmp_dir = tempfile.mkdtemp(prefix="kindle_mtp_")
        mount_point = Path(tmp_dir)
    mount_point.mkdir(parents=True, exist_ok=True)

    try:
        print(f"  Mounting via {tool} at {mount_point} …", file=sys.stderr)
        result = subprocess.run(
            [tool, str(mount_point)], capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"{tool} failed: {(result.stderr or result.stdout).strip()}"
            )
        docs = mount_point / "documents"
        if not docs.is_dir():
            _fuse_unmount(mount_point)
            raise RuntimeError("Mounted but 'documents/' not found — is a Kindle connected?")
        yield mount_point
    finally:
        _fuse_unmount(mount_point)
        if tmp_dir:
            import shutil as _sh
            _sh.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Filesystem-based book / clipping enumeration (used after OS or FUSE mount)
# ---------------------------------------------------------------------------

def list_device_books(kindle_root: Path) -> List[dict]:
    """List books from the Kindle's documents directory (filesystem access)."""
    documents = kindle_root / "documents"
    books: dict[str, dict] = {}

    for sdr in documents.rglob("*.sdr"):
        if not sdr.is_dir():
            continue
        title = sdr.stem
        entry = books.setdefault(title, {
            "title": title, "has_ebook": False, "has_sidecar": True,
            "clipping_files": [], "ebook_path": None, "sdr_path": sdr,
        })
        for f in sdr.rglob("*"):
            if f.is_file() and f.suffix.lower() in _ANNOTATION_EXTS:
                entry["clipping_files"].append(f)
        for ext in _EBOOK_EXTS:
            candidate = sdr.parent / (title + ext)
            if candidate.exists():
                entry["has_ebook"] = True
                entry["ebook_path"] = candidate
                break

    for f in documents.rglob("*"):
        if f.is_file() and f.suffix.lower() in _EBOOK_EXTS:
            title = f.stem
            if title not in books:
                books[title] = {
                    "title": title, "has_ebook": True, "has_sidecar": False,
                    "clipping_files": [], "ebook_path": f, "sdr_path": None,
                }

    return sorted(books.values(), key=lambda b: b["title"].lower())


def list_device_clippings_file(kindle_root: Path) -> Optional[Path]:
    """Return My Clippings.txt path on the device, or None."""
    p = kindle_root / "documents" / "My Clippings.txt"
    return p if p.exists() else None


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _print_book_table(books: List[dict],
                      clippings_txt: Optional[Path],
                      device_label: str) -> None:
    print(f"Kindle device: {device_label}")
    if clippings_txt:
        print(f"My Clippings.txt: {clippings_txt}")
    print()
    if not books:
        print("No books found.")
        return
    print(f"{'#':<5} {'Title':<55} {'Ebook':^7} {'Sidecar':^8} Annotations")
    print("-" * 90)
    for i, b in enumerate(books, 1):
        print(f"{i:<5} {b['title']:<55} "
              f"{'yes' if b['has_ebook'] else '-':^7} "
              f"{'yes' if b['has_sidecar'] else '-':^8} "
              f"{len(b['clipping_files']) or '-'}")
    print(f"\nTotal: {len(books)} book(s)")


def print_device_books(kindle_root: Path) -> None:
    """Print book table for a filesystem-mounted Kindle."""
    _print_book_table(
        list_device_books(kindle_root),
        list_device_clippings_file(kindle_root),
        str(kindle_root),
    )


def print_mtp_books(session: MTPDirectSession) -> None:
    """Print book table from an open MTPDirectSession."""
    _print_book_table(session.list_books(), clippings_txt=None,
                      device_label="MTP device")
