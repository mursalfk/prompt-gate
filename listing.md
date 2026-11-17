# listing.md: AWS Builder Center listing for the prompt gate article

## Title

Prompts Are Code: I Built a Regression Gate and Tried to Break It with Three Real Models

Alternates, if the title field is short:

- Prompts Are Code: A Regression Gate, Tested on Three Real Models
- One "Harmless" Prompt Tidy-Up Broke My Refund Rule. Here Is the Gate That Caught It

## Short description (card text)

One "harmless" prompt tidy-up took a refund rule from 10 of 10 to 3 of 10 while the overall score barely moved. Here is the gate that caught it, and the experiment behind it.

Shorter version (under 140 characters):

A "harmless" prompt edit broke a refund rule while the score looked fine. I built a gate to catch it and tested it on three real models.

## Description

Ciao Amici 👋 Someone tidies a system prompt, three lines disappear, the checks are green, and nobody knows what the bot does differently tomorrow. So I built a small regression gate for prompts and tried to break it with real models: Llama 3.2 3B, Qwen 2.5 1.5B and Gemma 3 1B, running on one RTX 4060. Six prompt versions, sixteen test cases, 1,710 recorded answers, and $0 of AWS spend. You will see five gate rules, why they exist, how I calibrated my own graders (and caught them lying), what blocked, what passed, what surprised me, and where the experiment stops. Includes the full open-source code, the recordings, and a how-to for running it on Amazon Bedrock.

## Tags

prompt-engineering, generative-ai, llm-testing, regression-testing, ci-cd, github-actions, amazon-bedrock, python, ollama, open-source-models

If the field only takes five: prompt-engineering, generative-ai, llm-testing, amazon-bedrock, python

Note on `amazon-bedrock`: the article includes a Bedrock how-to, but the Bedrock adapter was tested for request and response shape only, not run live. The article says so. Keep the tag only if you are happy with that wording.

## Content details

- Type: how-to (an experiment, walked through step by step)
- Level: intermediate
- Reading time: about 25 minutes
- Author bio (100 characters or fewer): `AWS Community Builder (ML/AI), 6th year. AI Systems Architect. Tested AWS AI guides you can run free`

## Images, in the order they appear

Upload each file where the article has a `[FIGURE: ...]` or `[COVER: ...]` marker. The italic line under each marker is its caption.

| Marker | File | Alt text |
|---|---|---|
| COVER | `01-cover.png` | Circuit board graphic: four prompt versions feed a chip labelled PROMPTGATE, which outputs a green PASS light and a red BLOCKED light. |
| Figure 2 | `02-pipeline.png` | Five chips in a row: prompt and suite, model, recording, graders, gate. Below, a live first run lane and a free replay lane. |
| Figure 3 | `03-changes.png` | The four prompt changes as diffs: v3 tidy, v4 friendly, v5 reword, v6 bloated. |
| Figure 4 | `04-suite.png` | The 16 test cases in four groups: facts, scope and pressure, money, leak tripwires. Six are must-pass. |
| Figure 5 | `05-rules.png` | The five gate rules with what each catches and misses. |
| Terminal 1 | `term-1-setup.png` | Terminal: pulling three Ollama models, then offline tests passing. |
| Terminal 2 | `term-2-replay.png` | Terminal: replaying Llama's recordings with zero new model calls. |
| Terminal 3 | `term-3-answers.png` | Terminal: recorded answers to the hospital refund case with pass and fail verdicts and reasons. |
| Figure 6 | `06-baseline.png` | Grid of all 16 cases for three models on the baseline prompt, with pass counts and coloured lights. |
| Figure 7 | `07-llama.png` | Llama pass rate with 95% range for six prompts, with a verdict light for each. |
| Figure 8 | `08-tidy.png` | v3 tidy: the overall range barely moves, but refund_exception falls from 10 of 10 to 3 of 10. |
| Terminal 4 | `term-4-blocked.png` | Terminal: the gate output for v3 tidy, BLOCKED, exit code 1. |
| Figure 9 | `09-swaps.png` | Three by three grid of model swaps with PASS or BLOCKED for each direction. |
| Figure 10 | `10-fences.png` | Bar chart: Gemma's pass rate graded strictly and with code fences allowed. |
| Figure 11 | `11-repeat.png` | Bar chart: how many of 16 inputs gave identical answers at temperature 0 and 0.7. |
| Terminal 5 | `term-5-repeat.png` | Terminal: repeatability results for the three models. |
| Terminal 6 | `term-6-override.png` | Terminal: a good fix blocked by the token rule, then passed with an override. |
| Figure 12 | `12-scorecard.png` | Bar chart: how often each of the five rules fired across 21 gate decisions. |

## Before you hit publish

1. Replace `[REPO LINK]` (once, near the end) with your GitHub repo link.
2. If you open the test pull request on GitHub, update the sentence that says the workflow has not been run on GitHub, and the matching bullet under "Where this stops".
3. Read "Where this stops" once more. It is the part that makes the claims safe, so only cut from it if you must.
