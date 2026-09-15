# B4 — autenticar a credencial Actions de cada consumidor

Continuação de [oon-docs#211](https://github.com/oondemand/oon-docs/issues/211),
após a [coleta Infra 35023881435/1](https://github.com/oondemand/oon-docs/issues/211#issuecomment-5688149907).
O publisher legado recusou todas as 26 consultas, inclusive criação de namespaces.
Essa prova usou impersonation; o vínculo com os Secrets Actions permanecia desconhecido.

## Operações preparadas

Após revisão, integração e CI, Fábio executa `inspect-publisher-credential`, branch
`main`, **sem inputs**, primeiro em `oondemand/central-ativacao`, depois em
`oondemand/oon-publish`. O conector não oferece workflow_dispatch. Guardar os dois
links de run/attempt; não usar os workflows de publicação para fazer essa inspeção.

Cada job resolve `secrets.PUBLISHER_KUBECONFIG_B64` no próprio repositório, sem
Environment, como os consumidores atuais `publish-central-dev.yml` e
`promote-environment.yml`. Não diferencia origem org/repo nem lê metadados do Secret:
observa a credencial efetivamente entregue ao processo. O código e os testes são
idênticos nos dois repositórios, evitando checkout privado de outro consumidor.

O inspector exige dispatch da main, SHA de workflow/execução coincidentes,
run/attempt exatos e permissão atual de escrita dos atores original e acionador.
Instala PyYAML 6.0.3 em venv antes de expor credenciais; não roda npm, kubectl,
plugin de autenticação, callback, token GitHub App ou OIDC.

## Referência e comportamento

Endpoint normalizado por URL.href e CA foram observados no run Infra acima:

| Campo | Valor |
|---|---|
| serverSha256 | f04d58a42ab3d711b7073420f599d66b902030e19abdded377d66d7824100a8f |
| caSha256 | 8da24d14528a8101737a0ec3d8009c572a8cfdefbed9de08653c1c378e25a637 |
| kubeSystemUid | 55ea77a1-6980-49e6-a6aa-ac19ff4dfb2c |

Hashes são fixados no código revisado, sem input para aceitar destino arbitrário.
Endpoint e CA devem coincidir **antes da conexão com credencial**; TLS valida o host.
Formato suportado: um cluster/contexto/usuário, endpoint HTTPS DNS/IPv4 sem caminho,
CA inline e token inline ou certificado/chave inline. Alias YAML, chaves duplicadas,
exec/auth-provider, arquivos externos, impersonation, proxy e TLS inseguro são recusados.
Não imprimir o kubeconfig para diagnosticar formato recusado; tratar apenas a estrutura,
sem valores de credenciais. Não há redirects nem uso de proxy do ambiente.

Consulta SelfSubjectReview sem impersonation; somente ServiceAccount com UID/grupos
esperados segue para a amostra de autorização. Identidade administrativa, grupo
system:masters ou publisher da raiz interrompem sem executar workloads. Grupos
arbitrários e campos extra de autenticação não entram no relatório. A identidade é
consultada novamente ao final; mudança interrompe. Cada permissão vem de
SelfSubjectAccessReview; erro de avaliação não vira negativa.
Inclui alvos nomeados de impersonation de usuários/grupos privilegiados e das
ServiceAccounts root-publisher, além de emissão de token dessas contas nas três
raízes. Grants limitados por resourceNames também precisam ser recusados.

O GET do UID de kube-system é opcional. Se a credencial receber 403, o relatório
marca `clusterUid=not-readable`; não conceder leitura de namespaces só para essa prova.
Se legível, o UID precisa coincidir. Endpoint/CA/TLS continuam obrigatórios.

## Resultado e parada

Esperado, se credencial existente suportada e amostra segura:
`credential-inspected-delivery-not-enabled`, `mutations=false`, sujeito autenticado,
UID, formato da credencial, amostra de autorização e run/attempt/SHA/atores.
Criação de namespaces é observação, não gate de aprovação para delivery.

Ausência de Secret retorna `B4_ACTIONS_CREDENTIAL_MISSING`; credencial recusada pelo
cluster retorna `B4_CREDENTIAL_UNAUTHORIZED`; formato ou cluster divergente bloqueia
antes de usar a credencial. `B4_FORBIDDEN_IDENTITY`, `B4_UNSAFE_SCOPE`, alteração de
identidade e autorização inconclusiva falham preservando o relatório parcial.
Não transformar falha em retry, rotação, concessão de RBAC ou cópia da credencial da raiz.
Relatório esperado: artefato `publisher-credential-report-1` na primeira tentativa.
Nunca compartilhar Secrets, kubeconfigs ou tokens para investigar.

Somente APIs de review sem persistência e GET são usadas. Certificado/chave, quando
necessários ao TLS, ficam em diretório temporário privado e são removidos ao sair;
não entram em artefatos. Nenhum Mongo, Secret Kubernetes, Deployment, binding,
variável Actions ou comando de entrega é escrito. Não há bootstrap ou recuperação.

O resultado não prova escopo positivo de membros, isolamento completo de ambientes,
estado atual do banco/delivery ou ausência de mudanças concorrentes de RBAC. Identidade
igual nos dois relatórios não prova token igual. A referência de configuração bloqueada
é a coleta anterior; revalidar a configuração persistida antes de qualquer futura mutação.

## Confiança e sequência seguinte

A integração na Central avança main e portanto github.workflow_sha. **Não atualizar
ROOT_TRUSTED_EXECUTORS nem republicar Prod só para executar este inspect**, que possui
guard próprio e não executa a raiz. Antes de qualquer operação futura da raiz,
revalidar a variável preservando os SHAs dos recibos. Os inspectors Infra anteriores
que exigem Central main igual ao SHA Prod deixarão de aceitar essa igualdade: não
trocar expected_sha pelo novo SHA como se já estivesse publicado. Revisar o verificador
específico somente quando uma nova operação Infra precisar dele; não repetir B3/B4.

Com ambos os relatórios, preparar executor dedicado e bindings mínimos com base nos
requisitos reais do SDK e nas identidades canônicas persistidas. Provar alvos permitidos
e recusas de membros/ambientes/instalações antes de remover blocked-pending-b5.yml.
Não reutilizar publisher da raiz ou reativar bindings legados. GET administrativo
funcional e jornada M2.3 continuam pendentes.

Referências: [SelfSubjectReview](https://kubernetes.io/docs/reference/access-authn-authz/authentication/#api-access-to-authentication-information-for-a-client)
e [SelfSubjectAccessReview](https://kubernetes.io/docs/reference/access-authn-authz/authorization/#checking-api-access).
