# Aide: Training Efficient Repository Explorer for Coding Agents

<p align="center">
  <a href="https://arxiv.org/abs/2606.14066"><img src="https://img.shields.io/badge/arXiv-2606.14066-b31b1b.svg" alt="arXiv"></a>
  <img src="https://img.shields.io/badge/Python-3.12%2B-blue.svg" alt="Python 3.12+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License"></a>
</p>

<p align="center">
  <a href="#news">📰 News</a> |
  <a href="#overview">🔎 Overview</a> |
  <a href="#results">📊 Results</a> |
  <a href="setup.md">⚙️ Setup</a> |
  <a href="#quick-start">⚡ Quick Start</a> |
  <a href="#reproduction">🧪 Reproduction</a> |
  <a href="#citation">📚 Citation</a>
</p>

Aide is a lightweight repository-exploration subagent for coding agents. Instead of letting the main
coding agent spend its own context window on broad file reads and code searches, the main agent delegates
a natural-language context query to Aide. Aide explores the repository with read-only tools,
issues independent tool calls in parallel, and returns compact file-line citations as focused evidence for the
main agent.

<p align="center">
  <img src="figures/overview.png" alt="Aide overview" width="95%">
</p>

## News

- 🚀 **2026-06-15**: We released the arXiv paper [[📄 arXiv](https://arxiv.org/abs/2606.14066)].


## Overview

Modern coding agents often use the same model to explore a repository and solve the task. This makes
exploration expensive: exploratory reads and searches consume tokens, stay in the solver's history, and can
pollute later reasoning with irrelevant snippets.

Aide separates repository exploration from solving:

- 🧭 **Delegated exploration**: the main agent asks Aide for repository context before editing or answering.
- 🔒 **Read-only tools**: Aide uses `Read`, `Glob`, and `Grep`; it does not modify files.
- ⚙️ **Parallel tool calling**: independent reads and searches can be issued in the same exploration turn.
- 📌 **Compact evidence**: the final response is a short `<final_answer>` block with file paths and line ranges.
- 🧠 **Trainable explorers**: the paper trains 4B-30B exploration models with SFT and task-grounded RL.

The intended contract is simple: Aide finds the relevant code; the main coding agent uses that focused
evidence to edit, test, or answer.

```text
<final_answer>
/path/to/repo/src/router.py:42-58
/path/to/repo/tests/test_router.py:101-119
</final_answer>
```

## Results

Across SWE-bench Multilingual, SWE-bench Pro, and SWE-QA, Aide improves the score-token tradeoff of
Mini-SWE-Agent style coding agents.

| Result | Finding |
| --- | --- |
| 📈 End-to-end success | Up to **+5.5** score improvement with delegated repository exploration. |
| 💸 Main-agent token use | Up to **60.3%** fewer main-agent tokens. |
| 🧠 Compact trained explorer | FC-4B-RL improves or ties FC-4B-SFT across all reported end-to-end settings. |
| 🎯 Standalone exploration | Trained Aide models recover patch-relevant files and symbols more accurately than non-Aide small-model baselines. |

<p align="center">
  <img src="figures/main-result.png" alt="Aide main results" width="95%">
</p>

## Token Efficiency

Aide reduces the main agent's context burden by moving broad repository exploration outside the
solver trajectory. The reduction is especially visible in file-reading and code-search tokens.

<p align="center">
  <img src="figures/breakdown.png" alt="Aide token breakdown" width="95%">
</p>

## Setup

See [setup.md](setup.md) for installation, model configuration, and verification instructions.

## Quick Start

Run Aide from the repository you want to explore:

```bash
aide \
  --query "Find the files that implement authentication and explain where to make a change" \
  --max-turns 6 \
  --traj .aide/trajectory.jsonl
```

Return only the machine-readable citation block:

```bash
aide \
  --query "Locate the request validation logic" \
  --citation
```

Useful CLI options:

| Option | Description |
| --- | --- |
| `--query`, `-q` | Natural-language exploration request. |
| `--traj`, `-t` | JSONL trajectory output path. |
| `--max-turns` | Maximum exploration turns before forcing a final answer. |
| `--verbose` | Print intermediate messages and runtime information. |
| `--citation` | Return only the `<final_answer>` block when present. |

## Programmatic Use

```python
import asyncio

from aide.agent.agent_factory import make_aide_agent


async def main() -> None:
    agent = make_aide_agent(
        trajectory_file=".aide/trajectory.jsonl",
        work_dir="/path/to/repo",
    )
    answer = await agent.run(
        prompt="Find where database migrations are defined",
        max_turns=6,
        citation=True,
    )
    print(answer)


asyncio.run(main())
```

## Reproduction

This repository contains scripts for end-to-end Mini-SWE-Agent runs and standalone exploration evaluation.
The exact paths, model names, and credentials should be adapted to your serving environment.

### End-to-End SWE-Bench Runs

```bash
git submodule update --init --recursive
uv build
cp benchmark/evaluation/configs/example.env .env
```

Edit `.env` with the main-agent and Aide endpoint credentials, then run:

```bash
uv run --group benchmark python benchmark/evaluation/bench_mini_swe_agent.py \
  --bench swebench-multilingual \
  --agent-config prompts/gpt-multi-fc.yaml \
  --config .env \
  --output preds.json \
  --logs-dir logs \
  --workers 1
```

For SWE-bench Pro, use the Pro prompt:

```bash
uv run --group benchmark python benchmark/evaluation/bench_mini_swe_agent.py \
  --bench ScaleAI/SWE-bench_Pro \
  --agent-config prompts/gpt-pro-fc.yaml \
  --config .env \
  --output preds-pro.json \
  --logs-dir logs-pro
```

### Standalone Exploration

The standalone runner evaluates Aide as a repository explorer on SWE-bench-style subagent queries.

```bash
cd benchmark/swebench
cp run.sh.sample run.sh
# Edit run.sh with BASE_URL, MODEL, and API_KEY.

uv run --group benchmark python bench_aide.py \
  --bench swebench-multilingual \
  --experiment aide-eval \
  --prediction-file predictions.jsonl \
  --local-mount-dir /absolute/path/to/output \
  --num-threads 1
```

After extracting the final Aide responses into a JSONL file with `instance_id` and `finial_response`
fields, score citation quality from the repository root:

```bash
uv run --group benchmark python benchmark/evaluation/run_score.py \
  swebench-multilingual \
  result_finial_response.jsonl
```

## Training and Serving

The `training/` directory contains scripts used for the SFT and RL experiments described in the paper.
These scripts assume a research training environment with external model checkpoints, datasets, and cluster
settings; treat paths and launcher options as examples to adapt.

```text
training/
  aide-sft/     Supervised fine-tuning scripts and data utilities
  aide-rl/      Reinforcement-learning scripts and reward utilities
```

The `serving/` directory contains example manifests and API checks for serving Aide-compatible
models behind an OpenAI-compatible endpoint.

## Repository Layout

```text
src/aide/
  cli.py                         Command-line entry point
  agent/
    agent.py                     Agent loop
    agent_factory.py             Default Aide agent construction
    context.py                   Conversation and trajectory storage
    llm.py                       OpenAI-compatible LLM wrapper
    system.md                    Explorer system prompt
    tool/
      read.py                    Read tool
      glob.py                    Glob tool
      grep.py                    Grep tool
      tool.py                    Tool base classes and ToolSet

benchmark/
  environment/                   Docker environment helpers
  evaluation/                    End-to-end Mini-SWE-Agent runners and scoring utilities
  swebench/                      SWE-bench-style standalone exploration runner

prompts/                         Mini-SWE-Agent prompt configs with Aide integration
training/                        SFT and RL training scripts
serving/                         Example serving manifests and API checks
tests/                           Unit and integration-style tests
figures/                         README and paper figures
```

## Development

Run linting:

```bash
uv run ruff check .
```

Run tests:

```bash
uv run pytest -q
```

Build the package:

```bash
uv build
```

## Notes

- Aide is intended for repository exploration, not code modification.
- Tool outputs are capped to keep interactions responsive.
- The default CLI records trajectories under `.aide/` unless `--traj` is provided.
- For best results, write specific exploration queries that name the behavior, subsystem, error, or files you are trying to locate.

## Citation

If you find Aide useful, please cite:

```bibtex
@misc{zhang2026aidetrainingefficientrepository,
      title={Aide: Training Efficient Repository Explorer for Coding Agents},
      author={Shaoqiu Zhang and Maoquan Wang and Yuling Shi and Yuhang Wang and Xiaodong Gu and Yongqiang Yao and Tori Gong and Sheng Chen and Rao Fu and Anisha Agarwal and Spandan Garg and Gabriel Ryan and Colin Merkel and Yufan Huang and Shengyu Fu},
      year={2026},
      eprint={2606.14066},
      archivePrefix={arXiv},
      primaryClass={cs.SE},
      url={https://arxiv.org/abs/2606.14066},
}
```

## Acknowledgements

Aide builds on open research infrastructure and benchmarks for coding agents, including SWE-bench,
SWE-bench Multilingual, SWE-bench Pro, SWE-QA, Mini-SWE-Agent, and open model / serving ecosystems.
