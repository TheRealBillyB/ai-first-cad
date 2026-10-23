"""Keep the process directory still while the geometry libraries load.

On Windows the virtual-environment redirector starts the base interpreter, so
the process image lives in that interpreter's directory. FreeImage's attach
routine, which runs when the geometry libraries load it, calls
``GetModuleFileNameW(NULL)`` and ``SetCurrentDirectoryW`` on that directory,
searches for a plugin, then restores the previous directory. Another thread
loses a relative open in between. The request for the interpreter directory
is refused. While that search is in progress a relative plugin is not loaded
from the project. Every other directory change is left alone.
"""

from __future__ import annotations

import sys
import threading

_PINNED = False
_LOCK = threading.Lock()


def interpreter_directory(module_path: str) -> str:
    """The directory of a module path, without a trailing separator."""
    slash = max(module_path.rfind("\\"), module_path.rfind("/"))
    if slash <= 0:
        return ""
    return module_path[:slash]


def pin_interpreter_directory() -> None:
    """Refuse a working-directory change into the running interpreter's folder.

    Installed once, before the geometry libraries are imported. Off Windows
    this does nothing. A failure to install the guard leaves the import to
    proceed; it does not change confinement.
    """
    global _PINNED
    if sys.platform != "win32" or _PINNED:
        return
    with _LOCK:
        if _PINNED:
            return
        try:
            _install_pin()
        except OSError:
            return
        _PINNED = True


def _install_pin() -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    kernel32.GetModuleFileNameW.restype = wintypes.DWORD
    kernel32.GetProcAddress.argtypes = [wintypes.HMODULE, wintypes.LPCSTR]
    kernel32.GetProcAddress.restype = ctypes.c_void_p
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.VirtualAlloc.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.VirtualAlloc.restype = ctypes.c_void_p
    kernel32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
    kernel32.VirtualFree.restype = wintypes.BOOL
    kernel32.VirtualProtect.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.VirtualProtect.restype = wintypes.BOOL
    kernel32.FlushInstructionCache.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t]
    kernel32.FlushInstructionCache.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE

    image = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetModuleFileNameW(None, image, len(image))
    if length == 0 or length >= len(image):
        raise OSError("the interpreter path is not available")
    folder = interpreter_directory(image.value)
    if not folder:
        raise OSError("the interpreter path has no directory")
    module = kernel32.GetModuleHandleW("kernel32")
    target = kernel32.GetProcAddress(module, b"SetCurrentDirectoryW")
    library = kernel32.GetProcAddress(module, b"LoadLibraryA")
    if not target or not library:
        raise OSError("SetCurrentDirectoryW is not available")
    if abs(library - target) > 0x40000000:
        raise OSError("the plugin load is too far from the directory call")
    directory_prologue = bytes((ctypes.c_char * 32).from_address(target))
    library_prologue = bytes((ctypes.c_char * 32).from_address(library))
    directory_stolen, directory_relocs = _steal(directory_prologue, 5)
    library_stolen, library_relocs = _steal(library_prologue, 5)
    page = _allocate_near(kernel32, target, 0x3000)
    if page is None:
        raise OSError("no executable page near the directory call")
    library_patched = False
    directory_patched = False
    try:
        folder_bytes = folder.encode("utf-16-le") + b"\x00\x00"
        if len(folder_bytes) > 0xE00:
            raise OSError("the interpreter directory does not fit")
        text_at = page
        trampoline_at = page + 0x180
        folder_at = page + 0x200
        library_at = page + 0x1000
        library_trampoline_at = page + 0x1180
        # The flag is written while the guard runs, so it stays off the executable pages.
        flag_at = page + 0x2000
        hook = _assemble_directory_hook(text_at, trampoline_at, folder_at, flag_at)
        library_hook = _assemble_library_hook(library_at, library_trampoline_at, flag_at)
        if len(hook) > 0x180 or len(library_hook) > 0x180:
            raise OSError("the directory guard does not fit")
        directory_trampoline = _trampoline(
            directory_prologue, directory_stolen, directory_relocs, target, trampoline_at
        )
        library_trampoline = _trampoline(
            library_prologue, library_stolen, library_relocs, library, library_trampoline_at
        )
        if len(directory_trampoline) > 0x70 or len(library_trampoline) > 0x70:
            raise OSError("the directory call prologue is too long")
        _write_page(
            kernel32,
            page,
            hook,
            directory_trampoline,
            folder_bytes,
            trampoline_at,
            folder_at,
            library_hook,
            library_trampoline,
            library_at,
            library_trampoline_at,
        )
        _patch_call(kernel32, library, library_stolen, library_at)
        library_patched = True
        _patch_call(kernel32, target, directory_stolen, text_at)
        directory_patched = True
    except BaseException:
        if directory_patched:
            raise
        if library_patched:
            _restore_call(kernel32, library, library_prologue[:library_stolen])
        kernel32.VirtualFree(page, 0, 0x8000)
        raise


def _allocate_near(kernel32, target: int, size: int):
    """A committed page within a direct jump of ``target``."""
    mem_commit = 0x1000
    mem_reserve = 0x2000
    page_readwrite = 0x04
    step = 0x100000
    start = max(0x10000, (target - 0x40000000) & ~(step - 1))
    stop = (target + 0x40000000) & ~(step - 1)
    address = start
    while address < stop:
        if abs(address - target) > 0x10000:
            page = kernel32.VirtualAlloc(_void(address), size, mem_commit | mem_reserve, page_readwrite)
            if page:
                return page
        address += step
    return None


def _void(address: int):
    import ctypes

    return ctypes.c_void_p(address)


def _trampoline(
    prologue: bytes,
    stolen: int,
    relocs: list[tuple[int, int, int]],
    target: int,
    trampoline_at: int,
) -> bytes:
    stolen_bytes = bytearray(prologue[:stolen])
    for disp_at, insn_at, insn_len in relocs:
        _retarget(stolen_bytes, disp_at, target + insn_at, insn_len, trampoline_at + insn_at)
    back = (target + stolen) - (trampoline_at + stolen + 5)
    if not -0x80000000 <= back <= 0x7FFFFFFF:
        raise OSError("the directory call is too far away")
    return bytes(stolen_bytes) + b"\xe9" + int(back).to_bytes(4, "little", signed=True)


def _write_page(
    kernel32,
    page: int,
    hook: bytes,
    trampoline: bytes,
    folder: bytes,
    trampoline_at: int,
    folder_at: int,
    library_hook: bytes,
    library_trampoline: bytes,
    library_at: int,
    library_trampoline_at: int,
) -> None:
    import ctypes

    # The page is writable until the bytes are in place, then executable.
    raw = (ctypes.c_char * 0x3000).from_address(page)
    raw[: len(hook)] = hook
    offset = trampoline_at - page
    raw[offset : offset + len(trampoline)] = trampoline
    text_at = folder_at - page
    raw[text_at : text_at + len(folder)] = folder
    library_offset = library_at - page
    raw[library_offset : library_offset + len(library_hook)] = library_hook
    library_tramp = library_trampoline_at - page
    raw[library_tramp : library_tramp + len(library_trampoline)] = library_trampoline
    protect = ctypes.c_uint32(0)
    if not kernel32.VirtualProtect(page, 0x2000, 0x20, ctypes.byref(protect)):
        raise OSError("the directory guard could not be made executable")
    kernel32.FlushInstructionCache(kernel32.GetCurrentProcess(), page, 0x2000)


def _assemble_directory_hook(
    hook_addr: int, trampoline_addr: int, folder_addr: int, flag_addr: int
) -> bytes:
    """Machine code: return success for the interpreter directory, else continue."""
    code = _Assembler()
    code.emit(0x48, 0x85, 0xC9)  # test rcx, rcx
    code.jcc_near(0x84, "real")
    code.emit(0x56)  # push rsi
    code.emit(0x57)  # push rdi
    code.emit(0x48, 0x89, 0xCE)  # mov rsi, rcx
    code.emit(0x48, 0xBF, *folder_addr.to_bytes(8, "little"))  # mov rdi, folder
    code.label("loop")
    code.emit(0x0F, 0xB7, 0x06)  # movzx eax, word ptr [rsi]
    code.emit(0x0F, 0xB7, 0x17)  # movzx edx, word ptr [rdi]
    code.emit(0x85, 0xC0)  # test eax, eax
    code.jcc(0x75, "check_pinned_end")
    code.emit(0x83, 0xFA, 0x5C)  # cmp edx, '\\'
    code.jcc(0x74, "pinned_sep")
    code.emit(0x83, 0xFA, 0x2F)  # cmp edx, '/'
    code.jcc(0x74, "pinned_sep")
    code.jmp("fold")
    code.label("pinned_sep")
    code.emit(0x66, 0x83, 0x7F, 0x02, 0x00)  # cmp word ptr [rdi+2], 0
    code.jcc(0x74, "ignore")
    code.jmp("fold")
    code.label("check_pinned_end")
    code.emit(0x85, 0xD2)  # test edx, edx
    code.jcc(0x75, "fold")
    code.emit(0x83, 0xF8, 0x5C)  # cmp eax, '\\'
    code.jcc(0x74, "asked_sep")
    code.emit(0x83, 0xF8, 0x2F)  # cmp eax, '/'
    code.jcc(0x74, "asked_sep")
    code.jmp("fold")
    code.label("asked_sep")
    code.emit(0x66, 0x83, 0x7E, 0x02, 0x00)  # cmp word ptr [rsi+2], 0
    code.jcc(0x74, "ignore")
    code.label("fold")
    code.emit(0x83, 0xF8, 0x41)  # cmp eax, 'A'
    code.jcc(0x72, "a_folded")
    code.emit(0x83, 0xF8, 0x5A)  # cmp eax, 'Z'
    code.jcc(0x77, "a_folded")
    code.emit(0x83, 0xC0, 0x20)  # add eax, 0x20
    code.label("a_folded")
    code.emit(0x83, 0xFA, 0x41)  # cmp edx, 'A'
    code.jcc(0x72, "d_folded")
    code.emit(0x83, 0xFA, 0x5A)  # cmp edx, 'Z'
    code.jcc(0x77, "d_folded")
    code.emit(0x83, 0xC2, 0x20)  # add edx, 0x20
    code.label("d_folded")
    code.emit(0x39, 0xD0)  # cmp eax, edx
    code.jcc(0x75, "real_pop")
    code.emit(0x85, 0xC0)  # test eax, eax
    code.jcc(0x74, "ignore")
    code.emit(0x48, 0x83, 0xC6, 0x02)  # add rsi, 2
    code.emit(0x48, 0x83, 0xC7, 0x02)  # add rdi, 2
    code.jmp("loop")
    code.label("ignore")
    # The first request arms the plugin guard. The paired request clears it.
    code.rip_mem_imm8(bytes((0x80, 0x3D)), flag_addr, hook_addr, 0)
    code.jcc(0x75, "clear_flag")
    code.rip_mem_imm8(bytes((0xC6, 0x05)), flag_addr, hook_addr, 1)
    code.jmp("return_true")
    code.label("clear_flag")
    code.rip_mem_imm8(bytes((0xC6, 0x05)), flag_addr, hook_addr, 0)
    code.label("return_true")
    code.emit(0xB8, 0x01, 0x00, 0x00, 0x00)  # mov eax, 1
    code.emit(0x5F)  # pop rdi
    code.emit(0x5E)  # pop rsi
    code.emit(0xC3)  # ret
    code.label("real_pop")
    code.emit(0x5F)  # pop rdi
    code.emit(0x5E)  # pop rsi
    code.label("real")
    code.rip_mem_imm8(bytes((0xC6, 0x05)), flag_addr, hook_addr, 0)
    code.rel32(0xE9, trampoline_addr, hook_addr)
    return code.bytes()


def _assemble_library_hook(hook_addr: int, trampoline_addr: int, flag_addr: int) -> bytes:
    """Refuse a relative ``.fip`` load while the directory request is in progress."""
    code = _Assembler()
    code.rip_mem_imm8(bytes((0x80, 0x3D)), flag_addr, hook_addr, 0)
    code.jcc_near(0x84, "original")
    code.emit(0x48, 0x85, 0xC9)  # test rcx, rcx
    code.jcc_near(0x84, "original")
    code.emit(0x56)  # push rsi
    code.emit(0x48, 0x89, 0xCE)  # mov rsi, rcx
    code.emit(0x80, 0x3E, 0x5C)  # cmp byte [rsi], '\\'
    code.jcc_near(0x84, "pass")
    code.emit(0x80, 0x3E, 0x2F)  # cmp byte [rsi], '/'
    code.jcc_near(0x84, "pass")
    code.emit(0xBA, 0x08, 0x02, 0x00, 0x00)  # mov edx, 0x208
    code.label("scan")
    code.emit(0x0F, 0xB6, 0x06)  # movzx eax, byte [rsi]
    code.emit(0x84, 0xC0)  # test al, al
    code.jcc(0x74, "ended")
    code.emit(0x3C, 0x3A)  # cmp al, ':'
    code.jcc(0x74, "pass")
    code.emit(0x48, 0xFF, 0xC6)  # inc rsi
    code.emit(0xFF, 0xCA)  # dec edx
    code.jcc(0x75, "scan")
    code.jmp("pass")
    code.label("ended")
    code.emit(0x48, 0x83, 0xEE, 0x04)  # sub rsi, 4
    code.emit(0x48, 0x39, 0xCE)  # cmp rsi, rcx
    code.jcc(0x72, "pass")
    code.emit(0x80, 0x3E, 0x2E)  # cmp byte [rsi], '.'
    code.jcc(0x75, "pass")
    code.emit(0x0F, 0xB6, 0x46, 0x01)
    code.emit(0x3C, 0x46)  # 'F'
    code.jcc(0x74, "f_ok")
    code.emit(0x3C, 0x66)  # 'f'
    code.jcc(0x75, "pass")
    code.label("f_ok")
    code.emit(0x0F, 0xB6, 0x46, 0x02)
    code.emit(0x3C, 0x49)  # 'I'
    code.jcc(0x74, "i_ok")
    code.emit(0x3C, 0x69)  # 'i'
    code.jcc(0x75, "pass")
    code.label("i_ok")
    code.emit(0x0F, 0xB6, 0x46, 0x03)
    code.emit(0x3C, 0x50)  # 'P'
    code.jcc(0x74, "refuse")
    code.emit(0x3C, 0x70)  # 'p'
    code.jcc(0x75, "pass")
    code.label("refuse")
    code.emit(0x5E)  # pop rsi
    code.emit(0x31, 0xC0)  # xor eax, eax
    code.emit(0xC3)  # ret
    code.label("pass")
    code.emit(0x5E)  # pop rsi
    code.label("original")
    code.rel32(0xE9, trampoline_addr, hook_addr)
    return code.bytes()


class _Assembler:
    def __init__(self) -> None:
        self._bytes = bytearray()
        self._labels: dict[str, int] = {}
        self._fixups: list[tuple[int, str, int]] = []
        self._near: list[tuple[int, str]] = []

    def emit(self, *values: int) -> None:
        self._bytes.extend(values)

    def label(self, name: str) -> None:
        self._labels[name] = len(self._bytes)

    def jcc(self, opcode: int, name: str) -> None:
        self._fixups.append((len(self._bytes), name, 1))
        self._bytes.extend((opcode, 0))

    def jcc_near(self, second: int, name: str) -> None:
        self._near.append((len(self._bytes), name))
        self._bytes.extend((0x0F, second, 0, 0, 0, 0))

    def jmp(self, name: str) -> None:
        self.jcc(0xEB, name)

    def rel32(self, opcode: int, absolute: int, hook_addr: int) -> None:
        at = len(self._bytes)
        self._bytes.extend((opcode, 0, 0, 0, 0))
        relative = absolute - (hook_addr + at + 5)
        if not -0x80000000 <= relative <= 0x7FFFFFFF:
            raise OSError("the directory call is too far away")
        self._bytes[at + 1 : at + 5] = int(relative).to_bytes(4, "little", signed=True)

    def rip_mem_imm8(self, opcodes: bytes, absolute: int, hook_addr: int, imm: int) -> None:
        self._bytes.extend(opcodes)
        disp_at = len(self._bytes)
        self._bytes.extend(b"\x00\x00\x00\x00")
        self._bytes.append(imm & 0xFF)
        rip = hook_addr + len(self._bytes)
        disp = absolute - rip
        if not -0x80000000 <= disp <= 0x7FFFFFFF:
            raise OSError("the directory guard cannot reach its flag")
        self._bytes[disp_at : disp_at + 4] = int(disp).to_bytes(4, "little", signed=True)

    def bytes(self) -> bytes:
        for at, name, size in self._fixups:
            if size != 1:
                raise OSError("unsupported branch")
            relative = self._labels[name] - (at + 2)
            if not -128 <= relative <= 127:
                raise OSError("the directory guard branch does not fit")
            self._bytes[at + 1] = relative & 0xFF
        for at, name in self._near:
            relative = self._labels[name] - (at + 6)
            if not -0x80000000 <= relative <= 0x7FFFFFFF:
                raise OSError("the directory guard branch does not fit")
            self._bytes[at + 2 : at + 6] = int(relative).to_bytes(4, "little", signed=True)
        return bytes(self._bytes)


def _steal(prologue: bytes, minimum: int) -> tuple[int, list[tuple[int, int, int]]]:
    """Bytes to lift off the directory call, and relative displacements to rewrite."""
    offset = 0
    relocs: list[tuple[int, int, int]] = []
    while offset < minimum:
        length, disp_at = _x64_instruction(prologue, offset)
        if disp_at is not None:
            relocs.append((offset + disp_at, offset, length))
        offset += length
        if offset > len(prologue):
            raise OSError("the directory call prologue is too short")
    return offset, relocs


def _retarget(stolen: bytearray, disp_at: int, original_at: int, insn_len: int, new_at: int) -> None:
    disp = int.from_bytes(stolen[disp_at : disp_at + 4], "little", signed=True)
    target = original_at + insn_len + disp
    rewritten = target - (new_at + insn_len)
    if not -0x80000000 <= rewritten <= 0x7FFFFFFF:
        raise OSError("a moved directory instruction cannot reach its target")
    stolen[disp_at : disp_at + 4] = int(rewritten).to_bytes(4, "little", signed=True)


def _x64_instruction(code: bytes, offset: int) -> tuple[int, int | None]:
    """Length of one x64 instruction, and the disp32 offset if it is relative."""
    start = offset
    rex_w = False
    operand_16 = False
    while offset < len(code):
        byte = code[offset]
        if byte in (0xF0, 0xF2, 0xF3, 0x2E, 0x36, 0x3E, 0x26, 0x64, 0x65):
            offset += 1
            continue
        if byte == 0x66:
            operand_16 = True
            offset += 1
            continue
        if byte == 0x67:
            raise OSError("an address-size prefix is not moved")
        if 0x40 <= byte <= 0x4F:
            rex_w = bool(byte & 0x08)
            offset += 1
            break
        break
    if offset >= len(code):
        raise OSError("the directory call prologue ended early")
    opcode = code[offset]
    offset += 1
    disp_at: int | None = None
    if opcode in (0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x5F, 0x90, 0xC3, 0xCC):
        return offset - start, None
    if opcode in (0xB8, 0xB9, 0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF):
        extra = 8 if rex_w else (2 if operand_16 else 4)
        return offset + extra - start, None
    if opcode in (0xE8, 0xE9):
        disp_at = offset - start
        return offset + 4 - start, disp_at
    if opcode == 0xEB or 0x70 <= opcode <= 0x7F:
        raise OSError("a short branch is not moved off the directory call")
    if opcode == 0x0F:
        raise OSError("a two-byte opcode is not moved off the directory call")
    modrm_ops = {
        0x01, 0x03, 0x09, 0x0B, 0x21, 0x23, 0x29, 0x2B, 0x31, 0x33, 0x39, 0x3B,
        0x80, 0x81, 0x82, 0x83, 0x85, 0x87, 0x88, 0x89, 0x8A, 0x8B, 0x8D, 0xC6, 0xC7, 0xFF, 0x8F,
    }
    if opcode not in modrm_ops:
        raise OSError("the directory call prologue is not recognised")
    if offset >= len(code):
        raise OSError("the directory call prologue ended early")
    modrm = code[offset]
    offset += 1
    mod = modrm >> 6
    rm = modrm & 7
    if rm == 4 and mod != 3:
        if offset >= len(code):
            raise OSError("the directory call prologue ended early")
        sib = code[offset]
        offset += 1
        if mod == 0 and (sib & 7) == 5:
            # Absolute disp32, not RIP-relative. The bytes can move unchanged.
            offset += 4
    if not (rm == 4 and mod != 3):
        if mod == 0 and rm == 5:
            disp_at = offset - start
            offset += 4
        elif mod == 1:
            offset += 1
        elif mod == 2:
            offset += 4
    elif mod == 1:
        offset += 1
    elif mod == 2:
        offset += 4
    immediate = 0
    if opcode in (0x80, 0x82, 0x83, 0xC6):
        immediate = 1
    elif opcode in (0x81, 0xC7):
        immediate = 2 if operand_16 else 4
    offset += immediate
    if offset > len(code):
        raise OSError("the directory call prologue ended early")
    return offset - start, disp_at


def _restore_call(kernel32, target: int, original: bytes) -> None:
    import ctypes

    old = ctypes.c_uint32(0)
    size = len(original)
    if not kernel32.VirtualProtect(target, size, 0x40, ctypes.byref(old)):
        return
    try:
        site = (ctypes.c_char * size).from_address(target)
        site[:size] = original
    finally:
        kernel32.VirtualProtect(target, size, old.value, ctypes.byref(old))
    kernel32.FlushInstructionCache(kernel32.GetCurrentProcess(), target, size)


def _patch_call(kernel32, target: int, stolen: int, guard_at: int) -> None:
    import ctypes

    relative = guard_at - (target + 5)
    if not -0x80000000 <= relative <= 0x7FFFFFFF:
        raise OSError("the directory call is too far away")
    patch = b"\xe9" + int(relative).to_bytes(4, "little", signed=True)
    patch += b"\x90" * (stolen - 5)
    old = ctypes.c_uint32(0)
    if not kernel32.VirtualProtect(target, stolen, 0x40, ctypes.byref(old)):
        raise OSError("the directory call could not be patched")
    try:
        site = (ctypes.c_char * stolen).from_address(target)
        site[:stolen] = patch
    finally:
        kernel32.VirtualProtect(target, stolen, old.value, ctypes.byref(old))
    kernel32.FlushInstructionCache(kernel32.GetCurrentProcess(), target, stolen)
