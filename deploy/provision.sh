#!/usr/bin/env bash
# Provision everything the commit reviewer needs in Azure. Safe to re-run.
#
#   Foundry (AI Services) account, key auth OFF -> Entra tokens only
#     └─ project            (unused by the reviewer for now; free, kept for evals later)
#     └─ guardrail policy   (Prompt Shields: jailbreak + indirect attack, blocking)
#     └─ model deployment   (gpt-5-mini, uses the guardrail policy)
#     └─ cost controls      (hard stop: $ per month, tokens per day)
#   Entra app + service principal, federated to GitHub Actions (no client secret)
#   Role "Cognitive Services OpenAI User" on the account for the app (and for me, for local runs)
#
# Usage: deploy/provision.sh
set -euo pipefail

SUBSCRIPTION="${SUBSCRIPTION:-$(az account show --query id -o tsv)}"
LOCATION="${LOCATION:-westus3}"          # westus2 no longer offers OpenAI models
RG="${RG:-commit-reviewer-rg}"
ACCOUNT="${ACCOUNT:-commit-reviewer-ai}"
PROJECT="${PROJECT:-commit-reviewer}"
DEPLOYMENT="${DEPLOYMENT:-reviewer}"
MODEL="${MODEL:-gpt-5-mini}"
MODEL_VERSION="${MODEL_VERSION:-2025-08-07}"
CAPACITY="${CAPACITY:-50}"               # thousand tokens per minute
RAI_POLICY="${RAI_POLICY:-reviewer-guardrails}"
MONTHLY_USD="${MONTHLY_USD:-5}"
DAILY_TOKENS="${DAILY_TOKENS:-3000000}"
APP_NAME="${APP_NAME:-commit-reviewer-github}"
# GitHub repo:branch pairs allowed to sign in as the reviewer.
GITHUB_SUBJECTS=(
  "repo:ericlatimer/whatsNext:ref:refs/heads/testing-amplify"
  "repo:ericlatimer/whats-next-cloud:ref:refs/heads/main"
)
API="2025-06-01"
ROLE="${ROLE:-Cognitive Services OpenAI User}"  # inference only: no keys, no management

az account set --subscription "$SUBSCRIPTION"
ARM="/subscriptions/$SUBSCRIPTION/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$ACCOUNT"

echo "== resource group"
az group create -n "$RG" -l "$LOCATION" -o none

echo "== Foundry account (key auth disabled)"
if ! az cognitiveservices account show -g "$RG" -n "$ACCOUNT" -o none 2>/dev/null; then
  az rest --method put --url "https://management.azure.com$ARM?api-version=$API" --body "{
    \"location\": \"$LOCATION\",
    \"kind\": \"AIServices\",
    \"sku\": {\"name\": \"S0\"},
    \"identity\": {\"type\": \"SystemAssigned\"},
    \"properties\": {
      \"customSubDomainName\": \"$ACCOUNT\",
      \"allowProjectManagement\": true,
      \"disableLocalAuth\": true,
      \"publicNetworkAccess\": \"Enabled\"
    }}" -o none
  until [ "$(az cognitiveservices account show -g "$RG" -n "$ACCOUNT" --query properties.provisioningState -o tsv)" = "Succeeded" ]; do sleep 5; done
fi

echo "== project"
az rest --method put --url "https://management.azure.com$ARM/projects/$PROJECT?api-version=$API" --body "{
  \"location\": \"$LOCATION\",
  \"identity\": {\"type\": \"SystemAssigned\"},
  \"properties\": {\"displayName\": \"$PROJECT\"}}" -o none

echo "== guardrail policy"
# Content harms set to High: diffs talk about 'killing' processes and 'executing' jobs, and a Medium
# threshold would block ordinary code. Prompt Shields (Jailbreak, Indirect Attack) block outright.
filters=""
for name in Hate Sexual Violence Selfharm; do
  for src in Prompt Completion; do
    filters+="{\"name\":\"$name\",\"blocking\":true,\"enabled\":true,\"severityThreshold\":\"High\",\"source\":\"$src\"},"
  done
done
filters+="{\"name\":\"Jailbreak\",\"blocking\":true,\"enabled\":true,\"source\":\"Prompt\"},"
filters+="{\"name\":\"Indirect Attack\",\"blocking\":true,\"enabled\":true,\"source\":\"Prompt\"}"
az rest --method put --url "https://management.azure.com$ARM/raiPolicies/$RAI_POLICY?api-version=$API" --body "{
  \"properties\": {\"basePolicyName\": \"Microsoft.DefaultV2\", \"mode\": \"Blocking\", \"contentFilters\": [$filters]}}" -o none

echo "== model deployment"
az rest --method put --url "https://management.azure.com$ARM/deployments/$DEPLOYMENT?api-version=$API" --body "{
  \"sku\": {\"name\": \"GlobalStandard\", \"capacity\": $CAPACITY},
  \"properties\": {
    \"model\": {\"format\": \"OpenAI\", \"name\": \"$MODEL\", \"version\": \"$MODEL_VERSION\"},
    \"raiPolicyName\": \"$RAI_POLICY\",
    \"versionUpgradeOption\": \"NoAutoUpgrade\"
  }}" -o none

echo "== cost controls (block requests at 100%)"
START="$(date -u +%Y-%m-01T00:00:00Z)"
END="$(date -u -v+1y +%Y-%m-01T00:00:00Z 2>/dev/null || date -u -d '+1 year' +%Y-%m-01T00:00:00Z)"
BLOCK='{actions:{stopRequests:{type:Block,thresholdType:Percentage,threshold:100,operator:GreaterThanOrEqualTo}}}'
for spec in "monthly-usd Cost $MONTHLY_USD Monthly" "daily-tokens Tokens $DAILY_TOKENS Daily"; do
  read -r name category amount grain <<<"$spec"
  verb=create
  az cognitiveservices account costcontrol show -g "$RG" --account-name "$ACCOUNT" -n "$name" -o none 2>/dev/null && verb=update
  az cognitiveservices account costcontrol "$verb" -g "$RG" --account-name "$ACCOUNT" -n "$name" \
    --category "$category" --amount "$amount" --time-grain "$grain" \
    --time-period "{start-date:'$START',end-date:'$END'}" \
    --provider-properties "$BLOCK" -o none --only-show-errors
done

echo "== Entra app, federated to GitHub Actions"
APP_ID="$(az ad app list --display-name "$APP_NAME" --query '[0].appId' -o tsv)"
if [ -z "$APP_ID" ]; then
  APP_ID="$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)"
fi
SP_ID="$(az ad sp show --id "$APP_ID" --query id -o tsv 2>/dev/null || az ad sp create --id "$APP_ID" --query id -o tsv)"
existing="$(az ad app federated-credential list --id "$APP_ID" --query '[].subject' -o tsv)"
for subject in "${GITHUB_SUBJECTS[@]}"; do
  grep -qxF "$subject" <<<"$existing" && continue
  cred_name="$(echo "$subject" | sed -E 's#repo:[^/]+/##; s#:ref:refs/heads/#-#; s#[^A-Za-z0-9-]#-#g')"
  az ad app federated-credential create --id "$APP_ID" --parameters "{
    \"name\": \"$cred_name\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"$subject\",
    \"audiences\": [\"api://AzureADTokenExchange\"]}" -o none
done

echo "== role assignments ($ROLE on the account)"
# The reviewer calls the account's OpenAI endpoint. The Foundry project endpoint refuses this role
# (and "Foundry Project Runtime User"); it wants "Foundry User", which can also list keys. Not worth it.
ME="$(az ad signed-in-user show --query id -o tsv)"
for principal in "$SP_ID:ServicePrincipal" "$ME:User"; do
  az role assignment create --role "$ROLE" --scope "$ARM" \
    --assignee-object-id "${principal%%:*}" --assignee-principal-type "${principal##*:}" -o none --only-show-errors
done

ENDPOINT="https://$ACCOUNT.openai.azure.com"
cat <<EOF

Done. GitHub repo variables for the workflow (not secrets; none of these grant access on their own):
  AZURE_CLIENT_ID=$APP_ID
  AZURE_TENANT_ID=$(az account show --query tenantId -o tsv)
  AZURE_SUBSCRIPTION_ID=$SUBSCRIPTION
  FOUNDRY_OPENAI_ENDPOINT=$ENDPOINT
  FOUNDRY_MODEL=$DEPLOYMENT
EOF
