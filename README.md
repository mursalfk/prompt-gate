# promptgate

A regression gate for prompt changes and model swaps. It runs a fixed set of test cases several times,
compares the result with a known-good baseline, and exits 1 when the change is worse, when a must-pass
case fails, or when the change quietly costs more tokens.

Standard library only. `boto3` is needed only for the Amazon Bedrock adapter.

## Try it

```
python test_promptgate.py            # the gate's own tests, fully offline

# with a local model server (Ollama shown; any OpenAI-compatible server works)
ollama pull llama3.2:3b
python -m promptgate run --suite examples/suite.json --prompt examples/prompts/v2_baseline.txt \
    --model openai:llama3.2:3b --samples 5 --cache cache.jsonl --out baseline.json
python -m promptgate run --suite examples/suite.json --prompt examples/prompts/v3_tidy.txt \
    --model openai:llama3.2:3b --samples 5 --cache cache.jsonl --out candidate.json
python -m promptgate gate --baseline baseline.json --candidate candidate.json    # exit 1 = blocked

# everything in the article: six prompts x three models, repeatability checks, summaries
ollama pull qwen2.5:1.5b && ollama pull gemma3:1b
python tools/run_all.py
# or step by step
python tools/run_matrix.py run --models qwen2.5:1.5b,llama3.2:3b,gemma3:1b
python tools/run_matrix.py summary
python tools/show_answers.py --model llama3.2:3b --prompt examples/prompts/v2_baseline.txt   # read every answer next to its verdict
```

## Models

| `--model` | What it calls | Cost |
|---|---|---|
| `openai:<name>` | Any OpenAI-compatible server. Default `--base-url` is Ollama's, `http://localhost:11434/v1`. | Free on your own machine |
| `ollama:<name>` | Ollama's native API | Free on your own machine |
| `bedrock:<model id>` | Amazon Bedrock Converse. Needs AWS credentials. | Billed per call |
| `fake-support` | A scripted model used only by this project's own tests | Free |

`--temperature` defaults to 0.7 and `--max-tokens` to 256. Test at the settings you ship.
Set `PROMPTGATE_API_KEY` if your OpenAI-compatible server needs a key.

## Cache

`--cache file.jsonl --cache-mode replay|record|auto`. Replay never calls a model, so CI is free.
The key is the model name, the full prompt, the input, the sample number and the sampling settings.

## Files

- `promptgate/models.py` adapters and the response cache
- `promptgate/graders.py` deterministic checks
- `promptgate/suite.py` suite loading and validation
- `promptgate/runner.py` runs a suite, produces a report
- `promptgate/gate.py` the five rules, Wilson interval, Markdown verdict
- `promptgate/cli.py` command line (exit 0 pass, 1 blocked, 2 usage or cache miss)
- `tools/run_all.py` one command: every prompt on every model, repeatability checks, summaries
- `tools/run_matrix.py` every prompt on every model, plus a summary
- `tools/show_answers.py` prints every recorded answer next to its grader verdict (how you calibrate graders)
- `tools/determinism_check.py` how repeatable your model server is
- `examples/suite.json` the 16 test cases; `examples/prompts/` the first try, the baseline and four changes
- `examples/support_bot.py` and `round1/` the scripted stand-in model and the older suite the offline tests use
- `results/` appears after `tools/run_all.py`: recorded answers, one report per model and prompt, the summary.
  Commit it and the pull-request workflow replays it for free.
- `.github/workflows/prompt-gate.yml` pull-request workflow

## What was and was not run

The numbers in the article come from `tools/run_all.py` on one machine. Your numbers will differ a little:
model builds, quantisation and sampling differ, which is the point of recording the answers.
The Bedrock adapter is tested for request and response shape with botocore's `Stubber`. It has not been
run against the live service.
