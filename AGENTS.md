# see-and-describe

Summarise long PDFs and images with the local fast-mm vision model. Use this
whenever you need to "read" a PDF or image you cannot otherwise parse: it
writes exhaustive markdown that a non-multimodal reader (you) can consume.

## Command

    C:\Python313\python.exe C:\Repos\see-and-describe\see-and-describe.py <file> [more files...]
    C:\Python313\python.exe C:\Repos\see-and-describe\see-and-describe.py --probe

Use C:\Python313\python.exe (pymupdf lives there). Files: .pdf or
.png/.jpg/.jpeg/.webp/.bmp. Pass several at once: the model swap happens once
for the whole batch.

## Output

Each input gets `<name>.summary.md` written NEXT TO IT (not in this repo).
Markdown only, no preamble.
- PDF: a section per chunk headed `## Pages A-B`; the model labels each page `### Page N`.
- Image: one block.
Written per chunk, so an abort leaves the finished chunks on disk.

## Model swap (how it is meant to be called)

The script stops EVERY running llama-server (ports 8081-8084) via keepalive,
boots the fast-mm server on :8084 (Unsloth-3.8-27B-fastmm, 64k ctx, mmproj in
VRAM), does the work, then restores whatever was running before. Only one
server runs at a time: text + mm together OOM the box.

Call it as ONE blocking tool call (bash). The swap, the vision work, and the
restore all happen inside that single call. The agent itself runs on the text
model (:8082), which the script kills and later restarts; the harness is
blocked on the tool call the whole time, so it never needs :8082 in the
middle. By the time the call returns, the text model is back and the agent
continues normally.

So do NOT deliberate about timeouts (let the call run to completion) and do
NOT worry about the agent's own model being stopped: it is designed to bring
the agent back after the vision section is done. `--probe` shows what is
running before/after.

## Sizing / timeouts

- PDFs are processed 15 pages per call (CHUNK_PAGES=15), rendered at 140 DPI,
  capped at 12000 output tokens per chunk. chunks = ceil(pages / 15).
- Each chunk is one fresh GPU inference, so total time is roughly linear in
  chunk count.
- A chunk that overflows context is auto-halved down to a single page (adds
  time); no action needed.
- Fresh context per chunk: no cross-chunk memory. Page numbers come from the
  prompt, so trust the `### Page N` labels.

Timeout planning:
- Do NOT set a bash timeout. Let the call block until it finishes. A timeout
  abort kills the swap mid-run and leaves the text model down, the worst
  outcome; the call is designed to run to completion and restore the model.
- Multi-hundred-page docs are slow on a single GPU (a 400+ page manual is
  easily an hour or more), so a fixed timeout is more likely to abort a good
  run than to protect anything.

## Reading the summary back

A big PDF's summary.md can be hundreds of KB. Do not read it whole.
- Grep for the section you need, or `read` with offset/limit.
- Locate content via the `## Pages A-B` and `### Page N` headers.
- Small PDFs (a handful of pages): reading the whole file is fine.

## Machine-specific paths

Constants at the top of see-and-describe.py. On opcv2:
- LLAMA_DIR / MM_BAT / QWEN_BAT: C:\Users\short\Desktop\llama
- KEEPALIVE_PS1: C:\Repos\keepalive\keepalive-launch.ps1
- MM_PORT 8084, MODEL_ID Unsloth-3.8-27B-fastmm

## Deps

keepalive (a `qwen-mm` profile hook that boots the fast-mm bat), pymupdf in
C:\Python313, Windows.

## Hygiene

Outputs are `<name>.summary.md` next to the inputs. Run it on files outside
this repo, or clean up after; `*.summary.md` is gitignored so a stray one
cannot be committed.
