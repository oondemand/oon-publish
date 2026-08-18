# Oon Publish

Ponte pública e sem credenciais privilegiadas para solicitar a publicação de Centrais Oon.

Este repositório contém apenas workflows reutilizáveis. O código da plataforma, o executor de deploy e os segredos de infraestrutura permanecem em repositórios privados.

## Publicação em desenvolvimento

No repositório da Central:

```yaml
name: publish-dev

on:
  push:
    branches: [main]

jobs:
  request:
    permissions:
      contents: read
      id-token: write
    uses: oondemand/oon-publish/.github/workflows/request-dev.yml@main
```

O workflow:

1. lê o `appCode` e as capabilities físicas requeridas do `oon.deploy.json`; capabilities funcionais são lidas do `central.app.json`;
2. solicita um token OIDC do GitHub vinculado ao repositório, branch, commit e workflow chamadores;
3. envia a solicitação para a Central de Ativações;
4. não recebe kubeconfig, token do registry, credencial do MongoDB ou chave do GitHub App.

A Central de Ativações valida a identidade OIDC, o App, a licença, o repositório e o commit antes de despachar o executor privado.

## Capabilities obrigatórias

```json
{
  "capabilities": {
    "pdfRendering": { "required": true, "minVersion": "1.0.0" }
  }
}
```

O workflow envia somente nomes e versões mínimas. A Central de Ativações bloqueia a publicação antes do deploy quando o ambiente não possui uma versão compatível e devolve uma mensagem para executar a reconciliação. Endpoint e credenciais do provider nunca entram no build, no repositório chamador ou nos logs deste workflow.

`core.transactional-email` é uma capability funcional, local ao App. Ela deve ser declarada em `central.app.json`, com sua política de escopo e templates em `capabilitySettings`, e nunca em `oon.deploy.json`. O workflow valida essa declaração antes de solicitar a publicação; chaves do provedor são configuradas somente na tela operacional do próprio App.

## Versionamento

Enquanto a primeira etapa está em homologação, os consumidores usam `@main`. Após estabilização, o workflow deve ser consumido por uma tag imutável, como `@v1` ou por SHA completo.
