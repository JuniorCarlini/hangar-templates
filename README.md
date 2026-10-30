# hangar-templates

Catálogo da comunidade de templates de serviço do
[Hangar](https://github.com/JuniorCarlini/hangar): PostgreSQL, Redis, n8n,
Uptime Kuma e companhia, prontos para criar com um clique no painel.

## Como o painel usa este repositório

- Cada versão do Hangar já vem com os templates embutidos e funciona sem
  internet. Este catálogo traz templates novos e versões mais novas **entre
  uma release e outra do Hangar**.
- A cada mudança em `templates/` na `main`, o CI confere tudo, empacota em
  `catalog.tar.gz` e assina com Ed25519 (`catalog.tar.gz.sig`). Junto vão os
  metadados assinados (`catalog.tar.gz.meta` e `.meta.sig`): o SHA-256 do
  arquivo, um número de sequência que só cresce e uma validade de 30 dias.
  Tudo fica na [última release](https://github.com/JuniorCarlini/hangar-templates/releases/latest).
- Uma vez por dia, o painel baixa esses arquivos, confere as assinaturas
  com a chave embutida no binário e **valida de novo cada template** com as
  mesmas regras dos embutidos. Assinado mas inseguro continua recusado. Um
  catálogo vencido ou com sequência menor que a já aceita também é recusado
  (ninguém consegue reapresentar um catálogo antigo), por isso o catálogo é
  assinado de novo toda semana.
- Template novo (id que o Hangar não traz) entra como está. Para um id que o
  Hangar já traz embutido, uma `version` maior só vale como **troca de tag
  ou digest da imagem**: qualquer outra diferença (outro repositório de
  imagem, env, comando, volumes...) é recusada e o painel fica com o
  embutido. No empate, vence o embutido. Mudar um template não mexe em
  serviços já criados.

## Propor um template

Abra um pull request com uma pasta nova em `templates/<id>/`:

```
templates/<id>/
  compose.yaml    # o serviço, com imagem de versão fixa, limites e healthcheck
  template.toml   # nome, descrição, variáveis e exports
  logo.svg        # logo simples, sem scripts nem links externos
  README.md       # opcional
```

O passo a passo e as regras estão no [CONTRIBUTING.md](CONTRIBUTING.md).
Antes de abrir o PR, rode `hangard template check templates/<id>`.

## O que os bots fazem

- **check**: em todo PR e push, roda os testes do atualizador e
  `hangard template check templates/` (a mesma validação do painel).
- **publish**: a cada push em `main` que mexe em `templates/` (e toda
  segunda-feira, para renovar a validade), confere, empacota, assina e
  publica uma release `catalog-AAAAMMDD-HHMMSS` marcada como a mais recente.
  Guarda as 5 últimas. A chave só existe no environment `catalog`, liberado
  apenas para a `main`.
- **update**: todo dia, procura a tag estável mais nova de cada imagem
  dentro da regra do [`updates.toml`](updates.toml) (só tags publicadas para
  amd64 **e** arm64), sobe a `version` do template, confere e abre (ou
  atualiza) um pull request no branch `bot/update-image-tags`. Nada é
  assinado antes de uma pessoa revisar e fazer o merge.
- O `hangard` usado no CI vem da última release do Hangar e só roda depois
  de conferir a assinatura Ed25519 do `release.json` e o SHA-256 do binário.
- As actions estão pinadas por commit; o Dependabot mantém os pins.

As regras do `updates.toml`: `patch` (mesmo major.minor), `minor` (mesmo
major, padrão para bancos e filas), `major` (qualquer versão mais nova, para
ferramentas sem estado) e `keep` (nunca mexe). O formato da tag é mantido:
`18` continua só major, `4.3-management` vira `4.4-management`, `v1.55`
mantém o `v`.

## Licença

Apache-2.0, como o Hangar. Os logos e marcas pertencem aos seus donos; veja
[`templates/NOTICE.md`](templates/NOTICE.md).
