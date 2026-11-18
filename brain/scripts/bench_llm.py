#!/usr/bin/env python3
"""Benchmark local VLMs on the Mac: load time, image prefill, generation tok/s, memory.

Ollama (default):
    ollama serve &                       # or the Ollama app
    python brain/scripts/bench_llm.py --pull
    python brain/scripts/bench_llm.py --models qwen3.5:4b,qwen3-vl:4b --runs 3 --sizes 640,1280

MLX (in-process via mlx_vlm, no server needed; pip install -e 'brain[mlx]'):
    python brain/scripts/bench_llm.py --backend mlx
    python brain/scripts/bench_llm.py --backend mlx-server --url http://127.0.0.1:8080   # running mlx_vlm.server

Per model it reports:
  load_s     cold load (model unloaded first; Ollama load_duration / mlx_vlm load())
  text       prefill tok/s and generation tok/s on a short text-only JSON prompt
  img@N      prompt tokens incl. image, image prefill time (ms) and gen tok/s for a Dofus-like
             screenshot downscaled to N px on its longest side
  mem_gb     Ollama /api/ps size (weights + KV) or mlx peak memory
Results are also written as JSON (--out). Keep 16 GB in mind: run with Dofus closed for
clean numbers, then once with Dofus open.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dofus_brain.llm import extract_json, load_prompt  # noqa: E402
from dofus_brain.llm.images import to_pil  # noqa: E402

OLLAMA_MODELS = ["qwen3.5:4b", "qwen3-vl:4b", "qwen3-vl:8b"]
MLX_MODELS = ["mlx-community/Qwen2.5-VL-3B-Instruct-4bit", "mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
              "mlx-community/Qwen3-VL-4B-Instruct-4bit", "mlx-community/Qwen3-VL-8B-Instruct-4bit"]
TEXT_PROMPT = ('Réponds en JSON {"items": [...]} : liste 5 ressources récoltables par un bûcheron '
               'dans Dofus, une par élément.')


def bench_image() -> "object":
    p = ROOT / "fixtures" / "dialog.png"
    if p.exists():
        return to_pil(p)
    from dofus_brain.config import load_json
    from dofus_brain.mock_render import DEFAULT_DIALOG, Renderer

    r = Renderer(load_json("grid"), load_json("regions"))
    return to_pil(r.dialog(DEFAULT_DIALOG["npc"], DEFAULT_DIALOG["text"], DEFAULT_DIALOG["options"]))


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


# ---------------------------------------------------------------------------- ollama
async def bench_ollama(a) -> list[dict]:
    from dofus_brain.llm.ollama import OllamaClient

    probe = OllamaClient(model="x", url=a.url or "http://127.0.0.1:11434", timeout_s=a.timeout)
    h = await probe.health()
    if not h.get("ok"):
        print(f"Ollama not reachable: {h}. Start it with `ollama serve` (or the app).")
        return []
    print(f"Ollama {h.get('version')}")
    have = set(await probe.list_models())
    await probe.aclose()
    models = a.models.split(",") if a.models else OLLAMA_MODELS
    img = bench_image()
    sizes = [int(s) for s in a.sizes.split(",")]
    results = []
    for m in models:
        if m not in have and not any(h.startswith(m) for h in have):
            if a.pull:
                import subprocess

                print(f"pulling {m} ...")
                subprocess.run(["ollama", "pull", m], check=False)
            else:
                print(f"skip {m}: not pulled (use --pull or `ollama pull {m}`)")
                continue
        c = OllamaClient(model=m, url=a.url or "http://127.0.0.1:11434", timeout_s=a.timeout, keep_alive="5m",
                         num_ctx=a.num_ctx, think=False)
        row: dict = {"backend": "ollama", "model": m}
        try:
            await c.unload()
            await asyncio.sleep(1.0)
            r = await c.chat("Réponds juste OK.", max_tokens=4)
            row["load_s"] = round((r.timings.load_ms or 0) / 1000, 2)
            text = []
            for _ in range(a.runs):
                r = await c.chat(TEXT_PROMPT, json_mode=True, max_tokens=a.max_tokens)
                text.append(r.timings)
            row["text"] = {"prompt_tok_s": med(t.prompt_tok_s for t in text), "tok_s": med(t.tok_s for t in text),
                           "total_ms": med(t.total_ms for t in text)}
            for s in sizes:
                c.max_image_side = s
                ts, ok = [], 0
                for _ in range(a.runs):
                    r = await c.chat(load_prompt("classify_screen"), images=[img], json_mode=True, max_tokens=a.max_tokens)
                    ts.append(r.timings)
                    try:
                        ok += extract_json(r.text).get("screen") == "dialog"
                    except Exception:
                        pass
                row[f"img@{s}"] = {"prompt_tokens": med(t.prompt_tokens for t in ts), "prefill_ms": med(t.prompt_ms for t in ts),
                                   "tok_s": med(t.tok_s for t in ts), "total_ms": med(t.total_ms for t in ts),
                                   "correct": f"{ok}/{a.runs}"}
            ps = await c.running()
            mine = next((p for p in ps if p.get("name") == m or p.get("model") == m), None) or \
                next((p for p in ps if p.get("name", "").startswith(m)), None)
            if mine:
                row["mem_gb"] = round(mine.get("size", 0) / 1e9, 2)
                row["vram_gb"] = round(mine.get("size_vram", 0) / 1e9, 2)
            if not a.keep_loaded:
                await c.unload()
        except Exception as e:
            row["error"] = str(e)
        finally:
            await c.aclose()
        print(json.dumps(row, ensure_ascii=False))
        results.append(row)
    return results


# ---------------------------------------------------------------------------- mlx
def bench_mlx_inproc(a) -> list[dict]:
    try:
        import mlx.core as mx  # type: ignore
        from mlx_vlm import generate, load  # type: ignore
        from mlx_vlm.prompt_utils import apply_chat_template  # type: ignore
        from mlx_vlm.utils import load_config  # type: ignore
    except Exception as e:
        print(f"mlx_vlm not importable ({e}); pip install -e 'brain[mlx]'")
        return []
    import tempfile

    models = a.models.split(",") if a.models else MLX_MODELS
    img = bench_image()
    sizes = [int(s) for s in a.sizes.split(",")]
    results = []
    for m in models:
        row: dict = {"backend": "mlx", "model": m}
        try:
            t0 = time.perf_counter()
            model, processor = load(m)
            config = load_config(m)
            row["load_s"] = round(time.perf_counter() - t0, 2)

            def gen(prompt: str, images: list[str]) -> dict:
                formatted = apply_chat_template(processor, config, prompt, num_images=len(images))
                t = time.perf_counter()
                out = generate(model, processor, formatted, images or None, max_tokens=a.max_tokens, verbose=False,
                               temperature=0.0)
                wall = (time.perf_counter() - t) * 1000
                d = {"total_ms": wall}
                for k in ("prompt_tokens", "prompt_tps", "generation_tokens", "generation_tps", "peak_memory"):
                    if hasattr(out, k):
                        d[k] = getattr(out, k)
                d["text"] = getattr(out, "text", out if isinstance(out, str) else "")
                return d

            text = [gen(TEXT_PROMPT, []) for _ in range(a.runs)]
            row["text"] = {"prompt_tok_s": med(t.get("prompt_tps") for t in text), "tok_s": med(t.get("generation_tps") for t in text),
                           "total_ms": med(t["total_ms"] for t in text)}
            for s in sizes:
                im = img.copy()
                im.thumbnail((s, s))
                path = Path(tempfile.mkdtemp()) / f"bench_{s}.png"
                im.save(path)
                ts = [gen(load_prompt("classify_screen"), [str(path)]) for _ in range(a.runs)]
                row[f"img@{s}"] = {
                    "prompt_tokens": med(t.get("prompt_tokens") for t in ts),
                    "prefill_ms": med((t["prompt_tokens"] / t["prompt_tps"] * 1000) if t.get("prompt_tps") else None for t in ts),
                    "tok_s": med(t.get("generation_tps") for t in ts), "total_ms": med(t["total_ms"] for t in ts)}
            row["mem_gb"] = med(t.get("peak_memory") for t in text)
            del model, processor
            mx.clear_cache() if hasattr(mx, "clear_cache") else None
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"
        print(json.dumps(row, ensure_ascii=False))
        results.append(row)
    return results


async def bench_mlx_server(a) -> list[dict]:
    from dofus_brain.llm.mlx import MLXClient

    c = MLXClient(model=a.models or MLX_MODELS[1], url=a.url or "http://127.0.0.1:8080", timeout_s=a.timeout)
    row: dict = {"backend": "mlx-server", "model": c.model, "load_s": None}
    try:
        print(await c.health())
        img = bench_image()
        text = [(await c.chat(TEXT_PROMPT, max_tokens=a.max_tokens)).timings for _ in range(a.runs)]
        row["text"] = {"ttft_ms": med(t.ttft_ms for t in text), "tok_s": med(t.tok_s for t in text)}
        for s in [int(x) for x in a.sizes.split(",")]:
            c.max_image_side = s
            ts = [(await c.chat(load_prompt("classify_screen"), images=[img], max_tokens=a.max_tokens)).timings for _ in range(a.runs)]
            row[f"img@{s}"] = {"prefill_ms(ttft)": med(t.ttft_ms for t in ts), "tok_s": med(t.tok_s for t in ts),
                               "total_ms": med(t.total_ms for t in ts)}
    except Exception as e:
        row["error"] = str(e)
    finally:
        await c.aclose()
    print(json.dumps(row, ensure_ascii=False))
    return [row]


def table(rows: list[dict], sizes: list[int]) -> None:
    hdr = f"{'model':45s} {'load_s':>6s} {'txt tok/s':>9s} " + " ".join(f"{'img@'+str(s)+' tok/prefill_ms/tok_s':>30s}" for s in sizes) + f" {'mem_gb':>6s}"
    print("\n" + hdr)
    for r in rows:
        if "error" in r:
            print(f"{r['model']:45s} ERROR {r['error'][:80]}")
            continue
        cells = []
        for s in sizes:
            d = r.get(f"img@{s}", {})
            cells.append(f"{str(d.get('prompt_tokens')) + '/' + str(d.get('prefill_ms', d.get('prefill_ms(ttft)'))) + '/' + str(d.get('tok_s')):>30s}")
        print(f"{r['model']:45s} {str(r.get('load_s')):>6s} {str(r.get('text', {}).get('tok_s')):>9s} " + " ".join(cells)
              + f" {str(r.get('mem_gb')):>6s}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", default="ollama", choices=["ollama", "mlx", "mlx-server"])
    ap.add_argument("--models", help="comma separated (default: qwen3.5 4b, qwen3-vl 4b/8b)")
    ap.add_argument("--url")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--sizes", default="640,1280", help="image longest side(s) in px")
    ap.add_argument("--max-tokens", type=int, default=96)
    ap.add_argument("--num-ctx", type=int, default=4096)
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--pull", action="store_true", help="ollama pull missing models")
    ap.add_argument("--keep-loaded", action="store_true")
    ap.add_argument("--out", default="bench_llm.json")
    a = ap.parse_args()
    if a.backend == "ollama":
        rows = asyncio.run(bench_ollama(a))
    elif a.backend == "mlx":
        rows = bench_mlx_inproc(a)
    else:
        rows = asyncio.run(bench_mlx_server(a))
    if rows:
        table(rows, [int(s) for s in a.sizes.split(",")])
        Path(a.out).write_text(json.dumps(rows, indent=2, ensure_ascii=False))
        print(f"\nwrote {a.out}")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
