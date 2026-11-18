"""Ask the local LLM something from the command line.

    python -m dofus_brain.llm "Décris cette capture" --image fixtures/dialog.png
    python -m dofus_brain.llm "Quel écran ?" --image shot.png --json
    python -m dofus_brain.llm "Bonjour" --backend mlx --model mlx-community/Qwen2.5-VL-3B-Instruct-4bit
    python -m dofus_brain.llm --health
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from ..config import load_json
from . import LLMError, load_prompt, make_llm


async def _run(a: argparse.Namespace) -> int:
    cfg = load_json("llm")
    if a.url:
        cfg.setdefault(a.backend or cfg.get("backend", "ollama"), {})["url"] = a.url
    if a.max_side:
        cfg["max_image_side"] = a.max_side
    if a.timeout:
        cfg["timeout_s"] = a.timeout
    llm = make_llm(cfg, backend=a.backend, model=a.model)
    try:
        if a.health:
            print(json.dumps(await llm.health(), indent=2))
            return 0
        prompt = a.prompt
        if a.classify:
            prompt = load_prompt("classify_screen")
        if not prompt:
            print("prompt required", file=sys.stderr)
            return 2
        images = a.image or []
        try:
            if a.json or a.classify:
                r = await llm.chat_json(prompt, images=images, max_tokens=a.max_tokens)
                out = json.dumps(r.data, ensure_ascii=False, indent=2)
            else:
                r = await llm.chat(prompt, images=images, max_tokens=a.max_tokens)
                out = r.text
        except LLMError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(out)
        t = r.timings.as_dict()
        print(f"\n# {llm.backend}:{llm.model} total={t['total_ms']}ms load={t['load_ms']}ms "
              f"prefill={t['prompt_tokens']}tok/{t['prompt_ms']}ms ({t['prompt_tok_s']} tok/s) "
              f"gen={t['eval_tokens']}tok ({t['tok_s']} tok/s) ttft={t['ttft_ms']}ms attempts={r.attempts}",
              file=sys.stderr)
        return 0
    finally:
        await llm.aclose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dofus_brain.llm", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", nargs="?")
    ap.add_argument("--image", action="append", help="image file (repeatable)")
    ap.add_argument("--backend", choices=["ollama", "mlx", "mock"])
    ap.add_argument("--model")
    ap.add_argument("--url")
    ap.add_argument("--json", action="store_true", help="JSON mode + robust parsing/retry")
    ap.add_argument("--classify", action="store_true", help="use the screen-classification prompt")
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--max-side", type=int, help="downscale images so the longest side <= N px")
    ap.add_argument("--timeout", type=float)
    ap.add_argument("--health", action="store_true")
    return asyncio.run(_run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
