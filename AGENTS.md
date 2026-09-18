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

## Model swap (read before running)

The script stops EVERY running llama-server (ports 8081-8084) via keepalive,
boots the fast-mm server on :8084 (Unsloth-3.8-27B-fastmm, 64k ctx, mmproj in
VRAM), does the work, then restores whatever was running before. Only one
server runs at a time: text + mm together OOM the box.

So it will kill and later restart whatever model you had up. Do not run it
while the user is mid-task on the text model. `--probe` shows what is running.

## Sizing / timeouts

- PDFs are processed 15 pages per call (CHUNK_PAGES=15), rendered at 140 DPI,
  capped at 12000 output tokens per chunk.
- Big PDFs are slow: 420 pages = 28 chunks, each a fresh GPU inference.
  Give the bash call a 7200s timeout.
- A chunk that overflows context is auto-halved down to a single page; no
  action needed.
- Fresh context per chunk: no cross-chunk memory. Page numbers come from the
  prompt, so trust the `### Page N` labels.

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
