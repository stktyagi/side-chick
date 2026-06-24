# Setup

## Prerequisites

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/) (package and environment manager)

## Install the CLI

```bash
git clone https://github.com/stktyagi/Aide.git
cd Aide
uv tool install .
```

After installation, the `aide` command is available globally.

## Development Setup

```bash
uv sync --all-groups
```

## Build a Local Wheel

```bash
uv build
```

The built wheel is written under `dist/`:

```text
dist/aide-0.1.0-py3-none-any.whl
```

## Model Configuration

Aide expects an OpenAI-compatible chat completions endpoint. Configure these environment variables:

```bash
export BASE_URL="https://your-endpoint.example/v1"
export MODEL="your-model-name"
export API_KEY="your-api-key"
```

For benchmark evaluation runs, additional credentials can be set through `AIDE_*` variables in a `.env` file. See `benchmark/evaluation/configs/example.env`.

## Verify Installation

```bash
aide --help
```

Run Aide on a repository:

```bash
cd /path/to/your/repo
aide --query "Find the authentication logic" --citation
```
