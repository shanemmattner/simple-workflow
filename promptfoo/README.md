# promptfoo — pipeline prompt evaluation

Test the pipeline's triage and review prompts against real Claude output using promptfoo.

## Setup

No install needed — promptfoo runs via npx. Requires `claude` CLI to be installed and authenticated.

## Run

```bash
cd promptfoo/
npx promptfoo@latest eval
```

## View results

```bash
npx promptfoo@latest view
```

Opens a browser dashboard showing pass/fail per test case.

## How it works

- `promptfooconfig.yaml` — test cases with input variables and output assertions
- `run-claude.sh` — exec provider wrapper that pipes prompts to `claude -p`

The exec provider calls `run-claude.sh` with the rendered prompt as an argument. The script pipes it to `claude -p --max-turns 1` and extracts the result text.

## Environment variables

- `PROMPTFOO_MODEL` — override the model (default: `sonnet`)

## Adding test cases

Add new entries under `tests:` in `promptfooconfig.yaml`. Each test needs:
- `description` — what the test checks
- `vars` — template variables (e.g. `issue_body`, `combined_diff`)
- `assert` — conditions the output must satisfy (`contains`, `not-contains`, `javascript`)
