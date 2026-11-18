# Local VLM comparison: qwen3-vl:4b-instruct vs qwen2.5vl:7b vs qwen3.5:4b

Run via `.tools/scratch/llm_eval.py` against Ollama at 127.0.0.1:11434. One run per model, cold load first (model unloaded before each run). Dofus was not driven (no mouse or keyboard input). Logs: `.tools/scratch/eval_run.log` and `.tools/scratch/eval_q35.log`.

| Metric | qwen3-vl:4b-instruct | qwen2.5vl:7b | qwen3.5:4b |
|---|---|---|---|
| Cold load (first call) | 4.5 s | 15.8 s | 4.6 s |
| Image prefill (screen classification) | 6.2 to 7.5 s, 1272 prompt tokens (about 190 tok/s) | 14.3 to 17.7 s, 1283 prompt tokens (about 75 to 85 tok/s) | 2.7 to 2.9 s, 839 prompt tokens (about 290 tok/s) |
| Generation speed | 28.5 to 32.0 tok/s | 17.7 to 23.3 tok/s | 35.4 to 36.4 tok/s |
| Classify wall time per screenshot | 8.8 to 13.8 s (first includes load) | 15.7 to 36.7 s (first includes load) | 4.5 to 12.0 s (first includes load) |
| Screen classification accuracy (6 fixtures) | 4/6 (fight→world, loading→unknown) | 3/6 (world_4 and world_5→unknown, fight→world) | 5/6 (fight→world) |
| Dialog NPC / text | correct / correct | correct / correct | correct / correct |
| Dialog options (normalized match) | 3/3 | 3/3 | 3/3 |
| Dialog wall time | 2.7 s | 3.1 s | 4.4 s |
| Model size from Ollama `/api/ps` (after load) | 3.72 GB | 1.43 GB | 4.99 GB |
| Peak RSS of ollama / llama-server processes (sampled) | 1.02 GB | 0.52 GB | 0.57 GB |

qwen3.5:4b was pulled (4.0 GB download) and run once with the same harness. Ollama reports it as the MLX runner (nvfp4, about 4.5B params) and as vision-capable.

## Notes

- **Peak RAM is an estimate.** The sampled RSS counts only resident pages of the ollama and llama-server processes, and mmap'd weights may not all show up. `/api/ps` and RSS disagree on which model is larger, so neither figure is reliable.
- **Accuracy is from one run on 6 images**, so a one-image difference is not statistically meaningful.
- **Prompt sizes differ:** qwen3.5:4b used 839 prompt tokens against 1272 to 1283 for the others, so the prefill comparison is not like for like.
- **Dialog extraction is the same for all three** after normalization. The only textual difference is the accent in "Ou"/"Où" on the banque option.
- Text-only tokens/s was not measured by `llm_eval.py`; the generation figures above are for image prompts.

## Takeaway

qwen3.5:4b is the best of the three on this set: it classified 5/6 (qwen3-vl:4b-instruct got 4/6), prefilled about 2.3x faster and generated about 15% faster. It is slower on the dialog call (4.4 s vs 2.7 s) and its loaded size is larger (about 5 GB vs 3.7 GB). This is one run on 6 images, so treat the accuracy gain as a lead, not a result.
