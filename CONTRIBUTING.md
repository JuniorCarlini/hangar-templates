# Como contribuir

Obrigado por ajudar o catálogo! Todo template novo ou corrigido entra por
pull request e passa pelas mesmas regras que o painel do Hangar usa.

## A pasta do template

Cada template é uma pasta `templates/<id>/`. O `id` usa minúsculas, números
e `-`, e é igual ao `id` do `template.toml`. A pasta só pode ter estes
arquivos (nada de subpastas, até 64 KB cada):

| arquivo | obrigatório | o que é |
|---|---|---|
| `compose.yaml` | sim | o serviço, no formato do Docker Compose (só as chaves permitidas) |
| `template.toml` | sim | metadados, variáveis, exports e endpoints |
| `logo.svg` | sim | o logo |
| `README.md` | não | notas para quem vai usar |

Comece copiando um template parecido (por exemplo `templates/postgres/`).

### compose.yaml

- Imagem com **tag de versão explícita** (nada de `latest`). O painel
  resolve para digest na criação.
- Limites de memória (16M ou mais) e de CPU em `deploy.resources.limits`.
- Volume nomeado e **healthcheck** em todo serviço com volume.
- Porta só em `expose`: nenhum template publica porta.
- Proibido: `privileged`, `docker.sock`, bind mount, `network_mode`,
  `devices`, `pid`/`ipc`, `build`. `cap_add` só da lista permitida.
- Senhas e chaves viram `${variavel}` com `generate = "secret"`.

### template.toml

```toml
id = "meu-servico"
version = 1                 # suba a cada mudança: por id, vence a maior
category = "database"
tags = ["sql"]
docs = "https://exemplo.org/docs/"
main = "meu-servico"        # o serviço do compose que recebe o nome do usuário
# min_hangar = "v0.6.0"     # opcional: Hangar mínimo que roda este template
name = { en = "My service", "pt-BR" = "Meu serviço" }
description = { en = "...", "pt-BR" = "..." }

[variables.user]
default = "app"
pattern = '^[a-z_][a-z0-9_]{0,62}$'
label = { en = "User", "pt-BR" = "Usuário" }

[variables.password]
generate = "secret"         # senha nunca tem valor padrão

[[exports]]
key = "URL"
value = "proto://${user}:${password}@${service}:1234"
secret = true
```

Chave desconhecida ou placeholder inexistente é erro. Nome e descrição
sempre em `en` e `pt-BR`, curtos, dizendo o que já vem seguro de fábrica.

### logo.svg

SVG bem formado com raiz `<svg>`, sem `<script>`, `<foreignObject>`,
atributos `on*`, links externos ou DTD. Use o
[Simple Icons](https://simpleicons.org) (CC0) com a cor da marca em `fill`;
se a marca não estiver lá, desenhe um ícone genérico. Nunca invente marca, e
registre a origem em `templates/NOTICE.md`.

## Conferir antes do PR

Baixe o `hangard` da [última release do
Hangar](https://github.com/JuniorCarlini/hangar/releases/latest) (ou
compile com `go build ./cmd/hangard`) e rode:

```sh
hangard template check templates/meu-servico   # um template
hangard template check templates/               # o catálogo inteiro
```

A saída diz o arquivo e a chave de cada problema. Depois suba o template num
Docker de teste e confira que ele fica "healthy".

Se mexer em `scripts/update_tags.py`, rode os testes:

```sh
python3 -m unittest discover -s scripts
```

## Atualização automática das imagens

Um bot propõe a troca das tags todo dia, num pull request, dentro da regra
do `updates.toml`; revise as notas das imagens antes do merge, porque o merge
publica e assina um catálogo novo. Ao adicionar um template, inclua uma
entrada para ele (sem entrada, vale `minor`):

```toml
[templates.meu-servico]
rule = "minor"   # patch | minor | major | keep
```

Use `minor` para tudo que guarda dados num formato que muda de um major
para outro (bancos, filas, buscas) e `major` só para ferramentas sem estado.
