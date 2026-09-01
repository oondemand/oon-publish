#!/usr/bin/env bash
set -euo pipefail

workflow=".github/workflows/request-dev.yml"

test -f "$workflow"
grep -Fq 'DEFAULT_ACTIVATION_API_URL: https://central-ativacao.oonapps.online/api' "$workflow"
grep -Fq 'configured="${INPUT_ACTIVATION_API_URL:-${ORGANIZATION_ACTIVATION_API_URL:-$DEFAULT_ACTIVATION_API_URL}}"' "$workflow"
grep -Fq 'Origem: `SaaS default`' "$workflow"
grep -Fq 'allowed_hosts = {"central-ativacao.oonapps.online"}' "$workflow"
grep -Fq 'parsed.path.rstrip("/") != "/api"' "$workflow"
grep -Fq 'parsed.port not in (None, 443)' "$workflow"
grep -Fq 'parsed.username or parsed.password or parsed.query or parsed.fragment' "$workflow"
grep -Fq 'OON_ACTIVATION_API_URL=https://central-ativacao.oonapps.online/api' README.md
grep -Fq '.accessMode == "authenticated_users"' "$workflow"
grep -Fq 'Arquitetura tenantless inválida' "$workflow"
grep -Fq 'none/common/authenticated_users/rbac/singleton' README.md
grep -Fq 'Meus Apps, Oon Workspace e Oon Docs' README.md
grep -Fq 'PlatformCapabilityRegistration' README.md
grep -Fq '.schemaVersion == 2' "$workflow"
! grep -Fq '.schemaVersion == 1' "$workflow"
! grep -Fq 'credenciais do provider' README.md
grep -Fq '.arquivada // false' "$workflow"
grep -Fq '.restauracao // false' "$workflow"
grep -Fq 'Publicação Dev inconsistente' "$workflow"
grep -Fq 'Publicação Dev aceita:' "$workflow"
grep -Fq '.error.details.persistenceEntity' "$workflow"
grep -Fq 'entidade: $persistence_entity' "$workflow"

if grep -R -n -E 'central-ativacao\.central\.oondemand\.online|legacy fallback|LEGACY_ACTIVATION_API_URL' \
  .github/workflows README.md; then
  echo "Oon Publish não pode referenciar o endpoint legado." >&2
  exit 1
fi

echo "Greenfield activation endpoint contracts ok"
