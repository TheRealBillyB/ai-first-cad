import subprocess, sys, os, textwrap
import pytest

PRE = textwrap.dedent('''
    import os, sys, threading, signal, faulthandler
    faulthandler.enable(all_threads=True)
    def mark(s): os.write(2, (s + "\\n").encode())
    def run_in_thread(fn, stack=None):
        if stack: threading.stack_size(stack)
        err = []
        def w():
            try: fn()
            except BaseException as e: err.append(repr(e))
        t = threading.Thread(target=w); t.start(); t.join()
        if err: mark("ERR " + err[0])
    def gm():
        import gmsh
        mark("init"); gmsh.initialize(readConfigFiles=False, interruptible=False)
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("p"); gmsh.model.occ.addBox(0,0,0,10,10,10); gmsh.model.occ.synchronize()
        mark("meshing"); gmsh.model.mesh.generate(3); mark("meshed")
        gmsh.finalize(); mark("finalized")
    def aic():
        from aicad import kernel
        from aicad.fea import _mesh_solid
        cube = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [0, 0, 5]}])
        mark("aicad meshing"); _mesh_solid(cube, 2.5, 1); mark("aicad meshed")
''')

RS = """
import ctypes
def sp_keep(fn):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.sigaction.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
    old = ctypes.create_string_buffer(512)
    libc.sigaction(signal.SIGPIPE, None, old)
    fn()
    libc.sigaction(signal.SIGPIPE, old, None)
"""
VARIANTS = {
 "aicad 6 threads in turn": "from aicad import kernel\nfor _ in range(6): run_in_thread(aic)",
 "aicad worker then main": "from aicad import kernel\nrun_in_thread(aic); aic()",
 "aicad main then 2 workers": "from aicad import kernel\naic(); run_in_thread(aic); run_in_thread(aic)",
 "aicad 6 threads again": "from aicad import kernel\nfor _ in range(6): run_in_thread(aic)",
 "aicad 6 threads 3": "from aicad import kernel\nfor _ in range(6): run_in_thread(aic)",
}

def test_diag():
    import platform
    out = [f"{platform.platform()} {platform.machine()} py{sys.version.split()[0]}"]
    for name, body in VARIANTS.items():
        code = PRE + body + "\nmark('DONE')\n"
        env = dict(os.environ, PYTHONFAULTHANDLER="1")
        try:
            p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=240, env=env)
            out.append(f"--- {name}: rc={p.returncode}\n{p.stderr[:1800]}\n{p.stdout[-300:]}")
        except subprocess.TimeoutExpired:
            out.append(f"--- {name}: TIMEOUT")
    assert False, "\n" + "\n".join(out)
