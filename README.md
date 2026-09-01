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

A Central de Ativações valida a identidade OIDC, o App, o repositório e o commit antes de despachar o executor privado. Apps tenant-scoped também passam pelos gates de licença/entitlement. Os Apps comuns Meus Apps, Oon Workspace e Oon Docs usam o lifecycle tenantless sem licença artificial e declaram o mesmo contrato `none/common/authenticated_users/rbac/singleton`. Restrições internas, como a visibilidade de documentos, pertencem ao RBAC do próprio App.

## Endpoint canônico e cutover de infraestrutura

A publicação Dev resolve a Central de Ativações nesta ordem:

1. input explícito `activation_api_url` do workflow reutilizável;
2. variável organizacional `OON_ACTIVATION_API_URL`;
3. endpoint SaaS padrão `https://central-ativacao.oonapps.online/api`.

Usuários e organizações que publicam no SaaS não precisam criar variáveis. Não existe fallback para a infraestrutura anterior.

O endpoint deve usar HTTPS, terminar em `/api` e apontar exatamente para o hostname greenfield:

```text
OON_ACTIVATION_API_URL=https://central-ativacao.oonapps.online/api
```

Credenciais na URL, query string, fragmento, porta alternativa e qualquer domínio legado são recusados. Inputs e variáveis organizacionais também devem apontar para `https://central-ativacao.oonapps.online/api`; outros hostnames são recusados.

## Capabilities obrigatórias

```json
{
  "capabilities": {
    "pdfRendering": { "required": true, "minVersion": "1.0.0" }
  }
}
```

O workflow envia somente nomes e versões mínimas. A Central de Ativações
bloqueia a publicação antes do deploy quando o ambiente não possui uma
`PlatformCapabilityRegistration` compatível e devolve uma mensagem para
executar a reconciliação. Endpoint e credenciais da implementação técnica nunca
entram no build, no repositório chamador ou nos logs deste workflow.

`core.transactional-email` é uma capability funcional, local ao App. Ela deve ser declarada em `central.app.json`, com sua política de escopo e templates em `capabilitySettings`, e nunca em `oon.deploy.json`. O workflow valida essa declaração antes de solicitar a publicação; chaves do provedor são configuradas somente na tela operacional do próprio App.

## Versionamento

Enquanto a primeira etapa está em homologação, os consumidores usam `@main`. Após estabilização, o workflow deve ser consumido por uma tag imutável, como `@v1` ou por SHA completo.

## Promoção entre ambientes

`promote-environment.yml` é acionado exclusivamente pela Central de Ativações.
Ele recebe um `release_id`, obtém por OIDC o contexto canônico e aplica em
Homologação ou Produção o `imageDigest` já validado em Dev. O workflow não faz
checkout do App, não executa build e não publica uma nova imagem.

O Control Plane recusa artefatos anteriores ao contrato neutro de ambiente,
operações concorrentes, ausência de RBAC/entitlement/licença e Produção sem o
aceite de Homologação quando exigido. Falhas preservam o histórico e podem ser
reconciliadas de forma idempotente pelo Meus Apps.


## Apps singleton tenantless e credenciais de Deployment

Para um App global, o contexto de delivery usa `lifecycleMode=global_singleton` e uma identidade operacional composta por `deploymentId` e `deploymentToken`. O token é mascarado imediatamente pelo workflow, permanece somente no arquivo temporário protegido e é removido no cleanup. Ele nunca é enviado em URL, output ou resumo.

O deploy global não executa activation code de cliente. O mesmo Deployment é reconciliado por App + ambiente em retries, promoções e rollbacks; o digest da promoção continua sendo o artefato imutável produzido em Dev. Apps tenant-scoped preservam o contrato `tenant_activation`.
