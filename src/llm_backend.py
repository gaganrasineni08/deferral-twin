"""
The language model behind llm_fn(system_prompt, question, context) -> str.

The model is frozen and only generates; nothing here trains anything
(HANDOFF.md section 2). The backend is chosen by `llm.backend` in
configs/config.yaml, or by the caller:

  mlx       Phi-3-mini 4-bit with Apple's MLX (mlx-lm). The Mac default.
  llamacpp  Phi-3-mini 4-bit GGUF in llama.cpp's llama-server. The Windows default: it uses
            the graphics card through Vulkan when there is one, otherwise the processor.
  cuda      Phi-3-mini 4-bit with bitsandbytes on an NVIDIA GPU (Colab, Phase 1 re-runs).
  stub      no model: echoes the closest stored answer, clearly labelled. For tests.
  auto      mlx on Apple Silicon, llamacpp for a .gguf model, cuda when a GPU is present, otherwise stub.

  from llm_backend import load
  llm = load("mlx", model="/path/to/Phi-3-mini-4k-instruct-4bit")
  llm.fn(system_prompt, question, context)          # what deferral.py calls
  for piece in llm.stream(system_prompt, question): # text as it is generated
      ...
"""
import atexit
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

MLX_DEFAULT = "mlx-community/Phi-3-mini-4k-instruct-4bit"
STOP_TEXT = ("<|end|>", "<|endoftext|>", "<|user|>", "<|assistant|>")
CUDA_DEFAULT = "microsoft/Phi-3-mini-4k-instruct"
MAX_NEW_TOKENS = 90


@dataclass
class LLM:
    name: str
    kind: str
    generate: Callable          # (system_prompt, question) -> str
    stream_fn: Optional[Callable] = None     # (system_prompt, question) -> iterator of text pieces
    batch: Optional[Callable] = None         # ([system_prompts], [questions]) -> [str]
    stand_in: bool = False
    load_seconds: float = 0.0
    info: dict = field(default_factory=dict)

    def fn(self, system_prompt, question, context=None):
        return self.generate(system_prompt, question)

    def stream(self, system_prompt, question):
        if self.stream_fn is None:
            yield self.generate(system_prompt, question)
            return
        yield from self.stream_fn(system_prompt, question)

    def batch_fn(self, system_prompts, questions):
        if self.batch is not None:
            return self.batch(system_prompts, questions)
        return [self.generate(s, q) for s, q in zip(system_prompts, questions)]


def _messages(system_prompt, question):
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": question}]


# ---------- MLX (Apple Silicon) ----------
def _mlx(model=MLX_DEFAULT, max_new_tokens=MAX_NEW_TOKENS):
    t0 = time.time()
    from mlx_lm import load as mlx_load, stream_generate
    try:
        from mlx_lm.sample_utils import make_sampler
        sampler = make_sampler(temp=0.0)          # greedy: same memory, same answer
    except ImportError:                           # older mlx-lm: greedy is the default
        sampler = None
    mdl, tok = mlx_load(str(model))

    def prompt(sp, q):
        return tok.apply_chat_template(_messages(sp, q), tokenize=False, add_generation_prompt=True)

    def stream(sp, q):
        kw = {"max_tokens": max_new_tokens}
        if sampler is not None:
            kw["sampler"] = sampler
        buf = ""
        for r in stream_generate(mdl, tok, prompt(sp, q), **kw):
            text = getattr(r, "text", r)          # GenerationResponse in mlx-lm >= 0.19
            if not text:
                continue
            # Phi-3 ends its turn with <|end|>, which mlx-lm doesn't treat as a stop token:
            # stop there instead of showing it. Hold back anything that could be its start.
            buf += text
            cut = min((buf.find(t) for t in STOP_TEXT if t in buf), default=-1)
            if cut >= 0:
                if buf[:cut]:
                    yield buf[:cut]
                return
            keep = max((len(buf) - buf.rfind("<")) if "<" in buf[-12:] else 0, 0)
            if len(buf) > keep:
                yield buf[:len(buf) - keep]
                buf = buf[len(buf) - keep:]
        if buf:
            yield buf

    def generate(sp, q):
        return "".join(stream(sp, q)).strip()

    name = "Phi-3-mini 4-bit · MLX"
    return LLM(name=name, kind="mlx", generate=generate, stream_fn=stream,
               load_seconds=time.time() - t0, info={"model": str(model)})


def mlx_peak_memory():
    """Peak Metal memory MLX has allocated, in bytes (None when MLX is absent)."""
    try:
        import mlx.core as mx
    except ImportError:
        return None
    for f in (getattr(mx, "get_peak_memory", None), getattr(getattr(mx, "metal", None), "get_peak_memory", None)):
        if f:
            try:
                return int(f())
            except Exception:
                pass
    return None


# ---------- llama.cpp (Windows) ----------
def find_llama_server(server=None):
    for c in (server, os.environ.get("DEFERRALTWIN_LLAMA_SERVER"), shutil.which("llama-server")):
        if c and Path(c).exists():
            return str(c)
    return None


def _free_port():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _tie_to_this_process(proc):
    """Windows: put llama-server in a job object that closes with this process, so a twin
    server that is killed (Task Manager, a crash) never leaves 3 GB of model running."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.OpenProcess.restype = wintypes.HANDLE

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                   "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]
    job = k32.CreateJobObjectW(None, None)
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000             # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))   # ExtendedLimitInformation
    h = k32.OpenProcess(0x0001 | 0x0100, False, proc.pid)      # PROCESS_TERMINATE | PROCESS_SET_QUOTA
    k32.AssignProcessToJobObject(job, h)
    k32.CloseHandle(h)
    return job                                                  # keep the handle open for our lifetime


def _llama_devices(server):
    """The GPUs llama-server can use (Vulkan), from --list-devices. Empty means the processor only."""
    try:
        r = subprocess.run([server, "--list-devices"], capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL, creationflags=0x08000000 if sys.platform == "win32" else 0)
    except (OSError, subprocess.SubprocessError):
        return []
    out, listing = [], False
    for line in (r.stdout + "\n" + r.stderr).splitlines():       # "Available devices:" then "  Vulkan0: NVIDIA ..."
        if line.strip().lower().startswith("available devices"):
            listing = True
        elif listing and re.match(r"^\s+[A-Za-z]+\d*: ", line):
            out.append(line.strip())
    return out


def _llamacpp(model, max_new_tokens=MAX_NEW_TOKENS, server=None, log=None, ctx=4096, gpu=None):
    t0 = time.time()
    exe = find_llama_server(server)
    if not exe:
        raise FileNotFoundError("llama-server wasn't found. Run setup again to download llama.cpp.")
    if not Path(str(model)).exists():
        raise FileNotFoundError(f"the language model file is missing: {model}")
    devices = _llama_devices(exe) if gpu is None else (["requested"] if gpu else [])
    logf = open(log, "a", encoding="utf-8") if log else subprocess.DEVNULL
    proc, url = None, None
    # The graphics card first when there is one; a driver that can't run it falls back to the processor.
    for layers in ([99, 0] if devices else [0]):
        port = _free_port()
        url = f"http://127.0.0.1:{port}"
        cmd = [exe, "-m", str(model), "--host", "127.0.0.1", "--port", str(port), "-c", str(ctx),
               "-np", "1", "-ngl", str(layers), "--no-webui"]
        if log:
            logf.write(f"\n---- {time.ctime()} $ {' '.join(cmd)}\n")
            logf.flush()
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                                creationflags=0x08000000 if sys.platform == "win32" else 0)
        job = _tie_to_this_process(proc)
        atexit.register(lambda p=proc: p.poll() is None and p.kill())
        deadline = time.time() + 600
        while proc.poll() is None and time.time() < deadline:
            try:
                with urllib.request.urlopen(url + "/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.3)
        if proc.poll() is None and time.time() < deadline:
            break
        if proc.poll() is None:
            proc.kill()
        proc = None
    if proc is None:
        raise RuntimeError("llama-server didn't start" + (f"; see {log}" if log else ""))
    on_gpu = bool(devices) and layers > 0

    def stream(sp, q):
        body = {"messages": _messages(sp, q), "stream": True, "temperature": 0.0, "top_k": 1,
                "max_tokens": max_new_tokens, "stop": list(STOP_TEXT), "cache_prompt": True}
        req = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    piece = (json.loads(data)["choices"][0].get("delta") or {}).get("content")
                except (ValueError, KeyError, IndexError):
                    continue
                if piece:
                    yield piece

    def generate(sp, q):
        return "".join(stream(sp, q)).strip()

    llm = LLM(name=f"Phi-3-mini 4-bit · llama.cpp ({'graphics card' if on_gpu else 'processor'})", kind="llamacpp",
              generate=generate, stream_fn=stream, load_seconds=time.time() - t0,
              info={"model": str(model), "server": exe, "gpu": devices[0] if on_gpu else None})
    llm._proc, llm._job = proc, job          # keep both alive as long as the model is
    return llm


# ---------- CUDA (Colab T4; kept for Phase 1 re-runs) ----------
def _cuda(model=CUDA_DEFAULT, max_new_tokens=MAX_NEW_TOKENS):
    t0 = time.time()
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    tok = AutoTokenizer.from_pretrained(model)
    mdl = AutoModelForCausalLM.from_pretrained(
        model, device_map="auto", trust_remote_code=True,
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                               bnb_4bit_compute_dtype=torch.bfloat16))
    mdl.eval()
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "left"

    @torch.inference_mode()
    def batch(system_prompts, questions):
        texts = [tok.apply_chat_template(_messages(s, q), tokenize=False, add_generation_prompt=True)
                 for s, q in zip(system_prompts, questions)]
        enc = tok(texts, return_tensors="pt", padding=True, truncation=True, max_length=3000).to(mdl.device)
        out = mdl.generate(**enc, max_new_tokens=max_new_tokens, do_sample=False,
                           pad_token_id=tok.pad_token_id)
        return [tok.decode(o[enc["input_ids"].shape[1]:], skip_special_tokens=True).strip() for o in out]

    return LLM(name="Phi-3-mini 4-bit · CUDA", kind="cuda", generate=lambda sp, q: batch([sp], [q])[0],
               batch=batch, load_seconds=time.time() - t0, info={"model": model})


# ---------- stand-in ----------
def _stub():
    """For tests and for clicking through the interface. Says plainly that it is a stand-in."""
    def generate(sp, q, ctx=None):
        return "(stand-in model: no language model is loaded)"

    llm = LLM(name="Stand-in (no model)", kind="stub", generate=generate, stand_in=True)
    # The stand-in can see the retrieved context, so tests can check that a
    # correction or a note reaches the answer.
    llm.fn = lambda sp, q, ctx=None: (
        ctx[0]["text"].split("\nA: ", 1)[-1].strip() + " (stand-in model: closest stored answer, not generated text)"
        if ctx else generate(sp, q))
    return llm


def is_apple_silicon():
    return sys.platform == "darwin" and platform.machine() == "arm64"


def load(kind="auto", model=None, max_new_tokens=MAX_NEW_TOKENS, server=None, log=None):
    if kind == "mlx":
        return _mlx(model or MLX_DEFAULT, max_new_tokens)
    if kind == "llamacpp":
        return _llamacpp(model, max_new_tokens, server=server, log=log)
    if kind == "cuda":
        return _cuda(model or CUDA_DEFAULT, max_new_tokens)
    if kind == "stub":
        return _stub()
    if kind != "auto":
        raise ValueError(f"unknown llm backend {kind!r} (mlx, llamacpp, cuda, stub, auto)")
    if is_apple_silicon() and not str(model or "").endswith(".gguf"):
        return _mlx(model or MLX_DEFAULT, max_new_tokens)
    if str(model or "").endswith(".gguf") and find_llama_server(server):
        return _llamacpp(model, max_new_tokens, server=server, log=log)
    try:
        import torch
        if torch.cuda.is_available():
            return _cuda(model if model and not Path(str(model)).exists() else CUDA_DEFAULT, max_new_tokens)
    except ImportError:
        pass
    return _stub()


def from_config(cfg, model_override=None, server=None, log=None):
    c = (cfg or {}).get("llm", {}) or {}
    kind = c.get("backend", "auto")
    model = model_override or (c.get("mlx_model") if kind in ("mlx", "auto") and is_apple_silicon()
                               else c.get("cuda_model"))
    return load(kind, model=model, max_new_tokens=c.get("max_new_tokens", MAX_NEW_TOKENS), server=server, log=log)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="auto")
    ap.add_argument("--model")
    ap.add_argument("--server", help="llama-server for --backend llamacpp")
    ap.add_argument("question", nargs="?", default="What do you do to relax?")
    a = ap.parse_args()
    llm = load(a.backend, a.model, server=a.server)
    print(f"{llm.name}  (loaded in {llm.load_seconds:.1f} s)")
    t0 = time.time()
    print(llm.fn("You are a helpful assistant. Answer in one sentence.", a.question, []))
    print(f"{time.time() - t0:.2f} s")
