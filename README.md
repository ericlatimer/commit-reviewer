# commit-reviewer

A second reviewer for repos that ship straight to a branch. After each push, it reads the commits that matter and
posts what it finds to a GitHub issue. It never blocks a push. Only open high-severity findings can hold a
deploy, and an override always exists. Design and reasoning: [DESIGN.md](DESIGN.md).

Built on **Microsoft Agent Framework** (Python) and a **Microsoft Foundry** model deployment, with **Prompt Shields**
on the deployment and **GitHub OIDC → Entra ID** sign-in (no stored keys anywhere).

## How a push is handled

1. **Triage (no model).** The repo's `.reviewer/rules.yaml` decides which files to skip, which area each file
   belongs to, and which deterministic checks to run (IAM wildcards, `DROP COLUMN`, SQL f-strings, hand-edited
   synced copies...). Small commits outside always-review areas stop here and cost nothing.
2. **Secrets (no model).** gitleaks scans the commit. A hit becomes a high finding and nothing is sent to the model.
3. **Review agent.** Gets the diff (wrapped in `<documents>` so Prompt Shields treats it as untrusted), the rules
   for the areas it touches, and two read-only tools (`read_file`, `search_repo`) that see the repo as of that
   commit. Returns typed findings (Pydantic schema).
4. **Report.** One comment per reviewed commit on a rolling "Reviewer findings" issue; each high finding gets its
   own issue labeled `severity:high` + `area:*`, with a mention so GitHub emails the owner. A JSON evidence record
   (model, prompt hash, tokens, findings) is uploaded with every run.

## Checkpoints

| Where | Blocks | Script |
|---|---|---|
| `pre-push` hook | the push, if gitleaks finds a secret | `hooks/pre-push-gitleaks.sh` |
| GitHub Action after push | nothing; it reports | `templates/commit-review.yml` |
| `deploy.sh` | the deploy, while a `severity:high` issue is open (`REVIEWER_OVERRIDE="reason"` to override, logged) | `hooks/deploy-gate.sh` |

## Azure (`deploy/provision.sh`, re-runnable)

- Foundry (AI Services) account with **key auth disabled**, a project, and a `gpt-5-mini` deployment (westus3).
- Guardrail policy `reviewer-guardrails`: Prompt Shields (jailbreak + indirect attack) blocking; content harms at
  High so ordinary code ("kill the process") isn't blocked.
- **Cost controls that block requests**, not just alert: $5/month and 1M tokens/day.
- Entra app with federated credentials for exactly two repo+branch pairs; no client secret exists.
- Role: *Cognitive Services OpenAI User* on the account (inference only: no keys, no management). The Foundry
  project endpoint would need *Foundry User*, which can list keys, so the reviewer calls the account endpoint.

## Run locally

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
deploy/install_gitleaks.sh                 # pinned version, checksum verified
.venv/bin/python -m pytest -q

export FOUNDRY_OPENAI_ENDPOINT=https://commit-reviewer-ai.openai.azure.com
export FOUNDRY_MODEL=reviewer              # signs in with your `az login`
.venv/bin/python -m commit_reviewer review --repo ../some-repo --rules ../some-repo/.reviewer/rules.yaml \
    --range HEAD~5..HEAD                   # add --no-agent for triage only, --post owner/name to post
```

## Adding a repo

1. Copy `templates/commit-review.yml` to `.github/workflows/`, set the branch.
2. Write `.reviewer/rules.yaml`: `skip_paths`, `areas` (paths, `always_review`, guidance for the model), `checks` (regex on added lines → finding), optional `synced` and `count_exclude_paths`. `tests/test_triage.py` has a compact example.
3. Add a federated credential for `repo:<owner>/<name>:ref:refs/heads/<branch>` (`GITHUB_SUBJECTS` in
   `provision.sh`) and set the five repo variables the workflow lists.
