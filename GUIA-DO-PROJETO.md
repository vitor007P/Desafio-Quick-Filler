# Guia do projeto — o que cada arquivo faz

Este documento explica, em linguagem simples, para que serve cada pasta e
cada arquivo do projeto. A ideia é que qualquer pessoa — mesmo sem conhecer o
código por dentro — consiga entender rapidamente "o que é isso" ao abrir o
repositório. Para detalhes técnicos de cada função, o código já tem
comentários explicando as decisões; aqui o foco é a visão de conjunto.

## Visão geral, em uma frase

O usuário envia um PDF (cartão de ponto ou holerite), o sistema lê o
documento (usando o texto do PDF ou OCR, se for uma página escaneada), mostra
uma tabela para revisar/corrigir lado a lado com o PDF original, e no final
gera uma planilha para download (`.xlsx`, `.csv` ou `.json`).

## O caminho que um documento percorre

```
1. Usuário sobe o PDF (tela de envio, web/)
        ↓
2. Servidor recebe, valida e enfileira (app/api/routes.py)
        ↓
3. Processamento em segundo plano (app/pipeline.py)
   → lê o PDF (app/core/pdfio.py) e faz OCR se precisar (app/core/ocr.py)
   → identifica datas/horas/valores (app/core/fields.py, uncertainty.py)
   → monta o resultado (app/extractors/cartao_ponto.py ou holerite.py)
        ↓
4. Resultado é salvo em disco (app/store.py)
        ↓
5. Tela de revisão busca a tabela pronta (app/grid.py) e mostra ao lado do PDF
        ↓
6. Usuário corrige o que precisar e baixa a planilha (app/spreadsheet/writers.py)
```

---

## Pasta `app/` — o backend (o "cérebro" do sistema)

Escrito em Python, usando o framework **FastAPI**. É o que recebe o PDF,
processa, guarda o resultado e serve a página web.

| Arquivo | Para que serve, em termos simples |
|---|---|
| `main.py` | O "liga/desliga" da aplicação. Cria o servidor, configura o log, agenda a limpeza automática dos documentos antigos e conecta tudo (as rotas da API e a pasta `web/`). |
| `config.py` | Todas as configurações ajustáveis do sistema (tamanho máximo de upload, tempo até apagar um documento, se o OCR está ligado, etc.), lidas de variáveis de ambiente. Nada fica "cravado" no código. |
| `models.py` | Define o formato exato dos dados: como é um "cartão de ponto" em JSON, como é um "holerite" em JSON, o que a API aceita e devolve. É o contrato entre o backend e quem consome a API. |
| `pipeline.py` | A fila de processamento. Quando um PDF é enviado, ele não é processado na hora — entra numa fila e é processado em segundo plano, para documentos grandes não travarem o sistema. |
| `store.py` | Onde e como os dados ficam salvos em disco (cada documento enviado vira uma pastinha própria). Também cuida de apagar automaticamente documentos antigos (por padrão, depois de 60 minutos). |
| `grid.py` | Monta a "tabela" que aparece na tela de revisão (e que também vira a planilha baixada) a partir dos dados extraídos, já calculando os avisos (ex.: "faltou uma batida", "essa data está fora de ordem"). |

### `app/api/` — os endereços (rotas) que o navegador chama

| Arquivo | Para que serve |
|---|---|
| `routes.py` | Cada "endereço" da API: enviar um PDF, consultar o andamento, corrigir manualmente, baixar a planilha, pegar o PDF original de volta, e o `/healthz` (usado pelo Docker/Vercel para saber se o sistema está de pé). |

### `app/core/` — as ferramentas usadas para "ler" o documento

| Arquivo | Para que serve |
|---|---|
| `pdfio.py` | Abre o PDF, confere se é válido, e decide, página por página, se dá para ler o texto direto do arquivo ou se precisa de OCR (quando a página é uma imagem escaneada). |
| `ocr.py` | O reconhecimento de imagem (OCR) propriamente dito, usando o programa **Tesseract**. Roda dentro do próprio computador/servidor — nenhum PDF é enviado para serviços externos, porque os documentos têm CPF e salário de pessoas reais. |
| `words.py` | Estrutura de dados comum: cada palavra encontrada no PDF (venha do texto nativo ou do OCR) vira um "objeto" com posição na página. É o que permite tratar PDF nativo e PDF escaneado do mesmo jeito no resto do sistema. |
| `fields.py` | Interpreta e "arruma" datas, horários e valores em dinheiro (ex.: transforma `08.03` em `08:03`), sempre guardando também o texto original como estava impresso. |
| `uncertainty.py` | A regra de honestidade do sistema: quando um caractere não pôde ser lido com confiança, ele vira `?` em vez de um palpite. Este arquivo decide quando marcar `?` e quando não. |

### `app/extractors/` — um "leitor" especializado por tipo de documento

| Arquivo | Para que serve |
|---|---|
| `base.py` | O que é comum aos dois tipos de documento: funções de apoio para comparar textos, calcular a área de uma palavra na página, etc. |
| `cartao_ponto.py` | Sabe ler especificamente um cartão de ponto: acha a coluna de datas, as colunas de entrada/saída, e separa isso das colunas de totais (que não são batidas do funcionário). |
| `holerite.py` | Sabe ler especificamente um holerite: separa as "verbas" (salário, horas extras, descontos) das "bases de cálculo" (Base INSS, Total Líquido, etc.), que são coisas diferentes. |

### `app/spreadsheet/`

| Arquivo | Para que serve |
|---|---|
| `writers.py` | Gera o arquivo final para download nos três formatos aceitos: `.xlsx` (com cores nas linhas com aviso), `.csv` e `.json`. Os três saem exatamente da mesma tabela, então nunca divergem entre si. |

---

## Pasta `web/` — o frontend (o que aparece na tela)

HTML, CSS e JavaScript "puros" — sem framework (React, Vue etc.) e sem etapa
de build. O próprio backend (`app/main.py`) serve esses arquivos direto.

| Arquivo | Para que serve |
|---|---|
| `index.html` | A estrutura da página: as três "telas" (enviar → processando → revisar), que aparecem uma de cada vez. |
| `app.js` | Todo o comportamento: envia o arquivo, fica perguntando ao servidor se já terminou, desenha a tabela editável, desenha o PDF na tela (usando a biblioteca `pdf.js`), salva as edições do usuário e aciona o download da planilha. |
| `estilo.css` | A aparência: cores, espaçamento, e o destaque em amarelo/vermelho nas linhas da tabela que têm algum aviso — as mesmas cores usadas na planilha `.xlsx`. |

---

## Pasta `tests/` — os testes automáticos

Garantem que o sistema continua funcionando corretamente depois de qualquer
mudança no código.

| Arquivo | Para que serve |
|---|---|
| `helpers.py` | Cria PDFs "de mentirinha" (sintéticos) para os testes usarem, já que documentos reais de pessoas não podem ficar salvos no repositório. |
| `test_api.py` | Testa se os endereços da API respondem do jeito certo (envio, consulta, correção, download). |
| `test_extractors.py` | Testa se a leitura do cartão de ponto e do holerite está extraindo os dados certos. |
| `test_grid.py` | Testa se a tabela de revisão e os avisos (data fora de ordem, batida ímpar, etc.) estão sendo calculados certo. |
| `test_uncertainty.py` | Testa a regra do `?` — que caracteres duvidosos são marcados e quais não são. |

Para rodar: `pytest -q` (com o ambiente virtual `.venv` ativado).

---

## Docker e deploy — como a aplicação "roda em algum lugar"

| Arquivo | Para que serve |
|---|---|
| `Dockerfile` | Receita para montar um "container" (uma caixinha com tudo que a aplicação precisa: Python, o Tesseract, as bibliotecas) para rodar **localmente** ou em qualquer serviço de hospedagem comum. Porta fixa em 8000. |
| `docker-compose.yml` | Facilita rodar essa receita com um único comando (`docker compose up`), já configurando as variáveis de ambiente e guardando os dados num espaço que sobrevive a reinícios. |
| `Dockerfile.vercel` | Uma variante do Dockerfile pensada especificamente para a **Vercel**: a porta vem da variável `$PORT` (a Vercel decide qual usar) e os arquivos ficam em `/tmp` (a única pasta gravável nesse tipo de hospedagem — não é armazenamento permanente). |
| `vercel.json` | Diz para a Vercel: "não tente rodar isso como uma função Python comum, construa e rode o `Dockerfile.vercel` como um container", e manda todo o tráfego do site para ele. |

## Dependências e configuração do projeto

| Arquivo | Para que serve |
|---|---|
| `requirements.txt` | Lista das bibliotecas Python que a aplicação precisa para funcionar (FastAPI, leitor de PDF, OCR, gerador de planilha, etc.), com a versão exata de cada uma — para o projeto se comportar igual em qualquer máquina. |
| `requirements-dev.txt` | As mesmas bibliotecas acima, mais as ferramentas usadas só durante o desenvolvimento (testes com `pytest`, checagem de estilo com `ruff`) — não precisam ir para produção. |
| `.gitignore` | Lista do que **não** deve ser enviado ao Git/GitHub: o ambiente virtual (`.venv`), caches, logs, e principalmente a pasta `data/` — onde ficam os PDFs e dados reais enviados pelos usuários (não podem vazar no repositório). |
| `.github/workflows/ci.yml` | Configuração de **integração contínua**: toda vez que alguém sobe código para o GitHub, essa automação roda o lint e os testes sozinha, para pegar erros antes de irem para produção. |

## Outras pastas

| Pasta | Para que serve |
|---|---|
| `data/` | Onde os PDFs enviados e as transcrições ficam salvos **durante a execução** (uma pasta por documento). Não existe no Git (está no `.gitignore`) — é gerada automaticamente quando a aplicação roda. |
| `exemplos/` | Reservada para documentos de exemplo; hoje está vazia. |
| `.venv/` | O ambiente virtual Python local, com todas as bibliotecas já instaladas — é o que permite rodar `uvicorn app.main:app` sem Docker. Também não vai para o Git. |

## Documentação

| Arquivo | Para que serve |
|---|---|
| `README.md` | O documento principal do projeto: o que ele faz, como rodar localmente (com ou sem Docker), como rodar os testes e como fazer o deploy na Vercel. É o primeiro lugar para consultar. |
| `GUIA-DO-PROJETO.md` | Este arquivo — o mapa de "o que é cada coisa", para quem quer entender a estrutura sem precisar ler o código inteiro. |
