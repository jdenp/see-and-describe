# see-and-describe

Summarise long PDFs and images with a local vision model. Each input gets
`<name>.summary.md` written next to it: exhaustive markdown for a
non-multimodal reader, fresh context per page-chunk / per image.

## Use

```
python see-and-describe.py <file> [file ...]   run the swap, summarise, restore
python see-and-describe.py --probe             show which llama servers are running
```

Files: .pdf or .png/.jpg/.jpeg/.webp/.bmp.

## How it works

The script swaps the local model itself: stops every running llama-server
(ports 8081-8084) via keepalive, boots the fast-mm server (:8084, 64k ctx,
mmproj in VRAM), summarises each input, then restores whatever was running
before. Only one server runs at a time: running text and mm models together
OOMs the box.

Summary files are written per chunk, so an abort leaves the finished chunks.

## Dependencies

- [keepalive](https://github.com/jdenp/keepalive) with a `qwen-mm` profile
  hook that boots the fast-mm launcher bat
- Python 3 with `pymupdf` (`pip install pymupdf`)
- Windows

Machine-specific paths are constants at the top of the script
(`LLAMA_DIR`, `MM_BAT`, `QWEN_BAT`, `KEEPALIVE_PS1`); edit them for your setup.
