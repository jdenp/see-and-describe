"""see-and-describe.py - summarise long PDFs and images with the local fast-mm model.

Usage:
  see-and-describe.py <file> [file ...]      run the swap, summarise, restore
  see-and-describe.py --probe                show which llama servers are running, exit

Files: .pdf or .png/.jpg/.jpeg/.webp/.bmp. Each input gets <name>.summary.md
written next to it (markdown, written for a non-multimodal reader).

Model swap: stops every running llama-server (ports 8081-8084) via keepalive,
boots the fast-mm server (Unsloth-3.8-27B_start_server-fast-mm.bat, :8084,
64k ctx, mmproj in VRAM) via keepalive, summarises each input with a fresh
context per page-chunk / per image, then restores whatever was running before.
Only one server runs at a time: running text and mm models together OOMs the box.
"""
import base64
import glob
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

LLAMA_DIR = r"C:\Users\short\Desktop\llama"
MM_BAT = os.path.join(LLAMA_DIR, "Unsloth-3.8-27B_start_server-fast-mm.bat")
QWEN_BAT = os.path.join(LLAMA_DIR, "Unsloth-3.8-27B_start_server.bat")
MM_PORT = 8084
ALL_PORTS = [8081, 8082, 8083, MM_PORT]
KEEPALIVE_PS1 = r"C:\Repos\keepalive\keepalive-launch.ps1"
MODEL_ID = "Unsloth-3.8-27B-fastmm"
API_URL = "http://127.0.0.1:%d/v1/chat/completions" % MM_PORT
DPI = 140
CHUNK_PAGES = 15
MAX_OUT = 12000
BOOT_TIMEOUT_S = 300
RESTORE_FALLBACK_S = 120
REQUEST_TIMEOUT_S = 1800
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".webp": "image/webp", ".bmp": "image/bmp"}

PDF_PROMPT = (
    "You are a document-to-text engine for a reader who cannot see the pages. "
    "The images are consecutive pages {first}-{last} of a {total}-page PDF. "
    "Transcribe and describe them exhaustively as markdown:\n"
    "- Reproduce all text in full, preserving structure (headings, lists, code).\n"
    "- Reproduce tables as markdown tables.\n"
    "- For each figure, chart or diagram: describe it precisely (type, axes, labels, "
    "values, legends, what it shows).\n"
    "- One section per page, headed '### Page N' where N is that page's number.\n"
    "No preamble, no meta commentary. Markdown only."
)
IMAGE_PROMPT = (
    "You are an image-to-text engine for a reader who cannot see the image. "
    "Describe this single image exhaustively as markdown:\n"
    "- All visible text, labels and values, verbatim.\n"
    "- Layout and composition; every distinct object or region.\n"
    "- If it is a screenshot, diagram, chart or table: reproduce its full content and "
    "structure (tables as markdown tables).\n"
    "No preamble, no meta commentary. Markdown only."
)


def log(msg):
    print("[snd] " + msg, flush=True)


def port_open(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def listener_pid(port):
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
    pids = []
    for line in out.splitlines():
        m = re.match(r"^\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)\s*$", line)
        if not m:
            continue
        proto, local, _foreign, state, pid = m.groups()
        if state != "LISTENING":
            continue
        # loopback or wildcard binds only: tailscaled also holds :8082 on the
        # tailscale IP and that is not the llama server
        if local not in ("127.0.0.1:%d" % port, "0.0.0.0:%d" % port):
            continue
        pids.append(int(pid))
    return pids[0] if pids else None


def cmdline(pid):
    ps = ("(Get-CimInstance Win32_Process -Filter \"ProcessId=%d\").CommandLine" % pid)
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return out or None
    except Exception:
        return None


def bat_for_cmdline(cl):
    # the bats pass relative -m paths, so resolve by gguf basename inside each bat;
    # sibling bats share the gguf, so disambiguate on the flags the cmdline shows
    cl = cl or ""
    m = re.search(r"-m\s+\"([^\"]+\.gguf)\"", cl) or re.search(r"-m\s+(\S+\.gguf)", cl)
    if not m:
        return None
    model = os.path.basename(m.group(1))
    ctx = re.search(r"--ctx-size\s+(\d+)", cl)
    offload = "--no-mmproj-offload" in cl
    cands = []
    for bat in glob.glob(os.path.join(LLAMA_DIR, "*.bat")):
        try:
            with open(bat, "r", errors="ignore") as f:
                txt = f.read()
        except Exception:
            continue
        if model not in txt:
            continue
        cands.append((bat, txt))
    for bat, txt in cands:
        bctx = re.search(r"--ctx-size\s+(\d+)", txt)
        if ctx and bctx and ctx.group(1) != bctx.group(1):
            continue
        if ("--no-mmproj-offload" in txt) != offload:
            continue
        return bat
    return cands[0][0] if cands else None


def discover_running():
    out = []
    for port in ALL_PORTS:
        if not port_open(port):
            continue
        pid = listener_pid(port)
        bat = bat_for_cmdline(cmdline(pid)) if pid else None
        out.append({"port": port, "pid": pid, "bat": bat})
        log("running on :%d (pid %s) bat=%s" % (port, pid, os.path.basename(bat) if bat else "?"))
    return out


def keepalive(*args, timeout=120):
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", KEEPALIVE_PS1] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def kill_port(port):
    pid = listener_pid(port)
    if not pid:
        return
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    deadline = time.time() + 10
    while port_open(port) and time.time() < deadline:
        time.sleep(0.25)
    if port_open(port):
        log("warning: :%d still open after kill" % port)


def stop_all():
    for profile in ("qwen", "qwen-mm"):
        keepalive(profile, "-k")
    for port in ALL_PORTS:
        kill_port(port)


def wait_port(port, timeout_s, what):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if port_open(port):
            return True
        time.sleep(2)
    log("error: %s did not come up on :%d within %ds" % (what, port, timeout_s))
    return False


def server_ready(port):
    # the http port opens before the model finishes loading; models stay empty until ready
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/v1/models" % port, timeout=5) as r:
            return bool(json.loads(r.read().decode()).get("data"))
    except Exception:
        return False


def boot_mm():
    if port_open(MM_PORT) and server_ready(MM_PORT):
        return True
    rc, out = keepalive("qwen-mm", "--detach")
    if rc != 0:
        log("error: keepalive qwen-mm --detach failed: %s" % out.strip())
        return False
    if not wait_port(MM_PORT, BOOT_TIMEOUT_S, "fast-mm server"):
        return False
    deadline = time.time() + BOOT_TIMEOUT_S
    while not server_ready(MM_PORT) and time.time() < deadline:
        time.sleep(2)
    if not server_ready(MM_PORT):
        log("error: fast-mm server on :%d never became ready" % MM_PORT)
        return False
    return True


def direct_launch(bat):
    # last-resort restore path when keepalive did not bring the port up
    flags = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    subprocess.Popen(["cmd.exe", "/c", bat], creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log("direct-launched %s" % os.path.basename(bat))


def restore(running):
    for r in running:
        port, bat = r["port"], r["bat"]
        if port == MM_PORT:
            keepalive("qwen-mm", "--detach")
        elif bat == QWEN_BAT or (bat is None and port == 8082):
            keepalive("qwen", "--detach")
        elif bat:
            keepalive(bat, "--detach")
        else:
            log("error: no known launcher for :%d, not restored" % port)
            continue
        if not wait_port(port, RESTORE_FALLBACK_S, "restore :%d" % port) and bat:
            direct_launch(bat)
            wait_port(port, BOOT_TIMEOUT_S, "restore :%d (direct)" % port)


def strip_think(text):
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.S).strip()


class Overflow(Exception):
    pass


def chat(images, prompt):
    # images: list of (mime, bytes); fresh context per call (no history)
    content = [{"type": "text", "text": prompt}]
    for mime, data in images:
        content.append({"type": "image_url",
                        "image_url": {"url": "data:%s;base64,%s" % (mime, base64.b64encode(data).decode())}})
    # thinking off explicitly: template default is off, pin it anyway
    body = json.dumps({"model": MODEL_ID, "max_tokens": MAX_OUT, "temperature": 0.2,
                       "chat_template_kwargs": {"enable_thinking": False, "preserve_thinking": True},
                       "messages": [{"role": "user", "content": content}]}).encode()
    for attempt in (1, 2):
        req = urllib.request.Request(API_URL, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                data = json.loads(resp.read().decode())
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise Overflow("output hit max_tokens")
            return strip_think(choice["message"]["content"])
        except urllib.error.HTTPError as e:
            errbody = e.read().decode(errors="replace")
            if e.code in (400, 413) and ("token" in errbody.lower() or "context" in errbody.lower()):
                raise Overflow(errbody)
            if attempt == 1:
                time.sleep(5)
                continue
            raise RuntimeError("HTTP %d: %s" % (e.code, errbody[:500]))
        except urllib.error.URLError:
            if attempt == 1:
                time.sleep(5)
                continue
            raise RuntimeError("cannot reach fast-mm server on :%d" % MM_PORT)


def render_pages(path, first, last):
    # returns [(mime, png_bytes)] for pages first..last (1-based, inclusive)
    import pymupdf
    doc = pymupdf.open(path)
    out = []
    for i in range(first - 1, last):
        pix = doc[i].get_pixmap(dpi=DPI)
        out.append(("image/png", pix.tobytes("png")))
    doc.close()
    return out


def describe_pdf_range(path, first, last, total):
    # halves the range on context overflow; minimum one page
    images = render_pages(path, first, last)
    prompt = PDF_PROMPT.format(first=first, last=last, total=total)
    try:
        return chat(images, prompt)
    except Overflow:
        if last - first < 1:
            raise RuntimeError("context overflow even on a single page of %s" % path)
        mid = (first + last) // 2
        a = describe_pdf_range(path, first, mid, total)
        b = describe_pdf_range(path, mid + 1, last, total)
        return a + "\n\n" + b


def summary_path(path):
    stem, _ = os.path.splitext(path)
    return stem + ".summary.md"


def summarise_pdf(path):
    import pymupdf
    n = pymupdf.open(path).page_count
    chunks = []
    for a in range(1, n + 1, CHUNK_PAGES):
        b = min(a + CHUNK_PAGES - 1, n)
        chunks.append((a, b))
    out = summary_path(path)
    # write per chunk: an abort leaves a usable partial summary
    with open(out, "w", encoding="utf-8") as f:
        f.write("# Summary of %s (%d pages)\n" % (os.path.basename(path), n))
        for i, (a, b) in enumerate(chunks, 1):
            log("%s: pages %d-%d (chunk %d/%d)" % (os.path.basename(path), a, b, i, len(chunks)))
            text = describe_pdf_range(path, a, b, n)
            f.write("\n\n## Pages %d-%d\n\n%s" % (a, b, text))
            f.flush()
            log("%s: chunk %d/%d done" % (os.path.basename(path), i, len(chunks)))
    return out, "%d pages in %d chunk(s)" % (n, len(chunks))


def summarise_image(path):
    mime = IMAGE_MIME[os.path.splitext(path)[1].lower()]
    with open(path, "rb") as f:
        data = f.read()
    log("%s: describing image" % os.path.basename(path))
    text = chat([(mime, data)], IMAGE_PROMPT)
    out = summary_path(path)
    with open(out, "w", encoding="utf-8") as f:
        f.write("# Summary of %s\n\n%s" % (os.path.basename(path), text))
    return out, "1 image"


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    if args[0] == "--probe":
        running = discover_running()
        if not running:
            log("no llama servers running")
        return 0

    files = []
    for a in args:
        p = os.path.abspath(a)
        if not os.path.isfile(p):
            log("error: not a file: %s" % p)
            return 2
        ext = os.path.splitext(p)[1].lower()
        if ext == ".pdf":
            files.append(("pdf", p))
        elif ext in IMAGE_MIME:
            files.append(("image", p))
        else:
            log("error: unsupported type %s (want .pdf or an image)" % ext)
            return 2

    def _abort(signum, frame):
        raise KeyboardInterrupt("aborted by signal %d" % signum)
    signal.signal(signal.SIGINT, _abort)
    signal.signal(signal.SIGTERM, _abort)
    running = discover_running()
    if not running:
        log("no llama servers running")
    log("stopping running servers")
    stop_all()
    results = []
    try:
        if not boot_mm():
            log("error: fast-mm server failed to start, aborting")
            return 1
        log("fast-mm up on :%d" % MM_PORT)
        for kind, p in files:
            try:
                if kind == "pdf":
                    out, desc = summarise_pdf(p)
                else:
                    out, desc = summarise_image(p)
                results.append((p, out, desc, None))
                log("wrote %s (%s)" % (out, desc))
            except Exception as e:
                results.append((p, None, None, str(e)))
                log("error on %s: %s" % (os.path.basename(p), e))
    except KeyboardInterrupt:
        log("aborted: stopping fast-mm and restoring previous server(s)")
        return 130
    finally:
        log("stopping fast-mm server")
        keepalive("qwen-mm", "-k")
        kill_port(MM_PORT)
        if running:
            log("restoring previous server(s)")
            restore(running)

    ok = all(e is None for _, _, _, e in results)
    log("done: %d/%d summarised" % (sum(1 for r in results if r[3] is None), len(results)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
