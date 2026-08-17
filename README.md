# Transcrição de documentos trabalhistas

Serviço web que recebe um PDF de **cartão de ponto** ou **holerite**, extrai os
dados (usando a camada de texto do PDF e, quando necessário, OCR com
Tesseract), mostra uma tela de revisão lado a lado com o documento original e
gera a planilha final em `.xlsx`, `.csv` ou `.json`.

## Como funciona

1. **Envio** — o usuário sobe o PDF e escolhe o tipo de documento.
2. **Processamento** — o servidor enfileira o job e processa em segundo
   plano: lê a camada de texto nativa do PDF ou, se a página for uma imagem
   escaneada, roda OCR (Tesseract) nela.
3. **Revisão** — a interface mostra o PDF ao lado de uma tabela editável, com
   avisos automáticos (batidas ímpares, data fora de sequência, mês fora de
   sequência, caractere não lido) destacados em amarelo/vermelho.
4. **Download** — a mesma tabela vira planilha, nos três formatos.

Nenhum dado é enviado para serviços de terceiros: o OCR roda localmente
dentro do próprio container, porque os documentos trazem CPF, matrícula e
salário de pessoas reais.

## Stack

- **Backend:** Python 3.12 + FastAPI + PyMuPDF (leitura de PDF) + Tesseract/
  pytesseract (OCR) + openpyxl (planilha).
- **Frontend:** HTML + CSS + JavaScript puro (sem framework/build), servido
  diretamente pelo FastAPI a partir da pasta `web/`.
- **Testes:** pytest (29 testes, sem dependência do binário do Tesseract —
  usam PDFs sintéticos com camada de texto).

## Rodando localmente

### Com Docker (recomendado — já inclui o Tesseract)

```bash
docker compose up --build
```

Acesse **http://localhost:8000**.

### Sem Docker

Requer o Tesseract instalado no sistema (`tesseract-ocr` + `tesseract-ocr-por`
no Linux, ou o instalador do [UB-Mannheim](https://github.com/UB-Mannheim/tesseract/wiki)
no Windows).

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # Linux/Mac

pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Acesse **http://127.0.0.1:8000**.

### Testes e lint

```bash
pytest -q
ruff check app tests
```

## Configuração (variáveis de ambiente)

Todas opcionais — os defaults valem para desenvolvimento local. Ver
`app/config.py` para a lista completa e os valores padrão. As mais comuns:

| Variável             | Padrão   | Descrição                                          |
| -------------------- | -------- | --------------------------------------------------- |
| `STORAGE_DIR`         | `./data` | Onde os PDFs e transcrições ficam salvos            |
| `RETENTION_MINUTES`   | `60`     | Tempo até apagar automaticamente uma transcrição    |
| `MAX_UPLOAD_MB`       | `20`     | Tamanho máximo do PDF                                |
| `OCR_ENABLED`         | `true`   | Liga/desliga o OCR para páginas escaneadas          |
| `OCR_LANG`            | `por`    | Idioma do Tesseract                                  |
| `CORS_ORIGINS`        | (vazio)  | Origens liberadas, separadas por vírgula            |

## Deploy na Vercel

O backend usa Tesseract (binário de sistema) e processamento assíncrono, que
não são compatíveis com a runtime Python "pura" da Vercel (baseada só em
pacotes pip, sem apt-get). Por isso o deploy usa **container Function**: a
Vercel constrói e roda a imagem definida em `Dockerfile.vercel` — o mesmo
ambiente do Docker local, só que a porta vem de `$PORT`.

```bash
npm i -g vercel
vercel login
vercel deploy --prod
```

O arquivo `vercel.json` já direciona todo o tráfego para esse container.

**Limitação importante:** containers de função rodam em disco temporário —
não há garantia de que os arquivos sobrevivam entre execuções ou instâncias.
`Dockerfile.vercel` aponta `STORAGE_DIR` para `/tmp`, o que é suficiente para
o ciclo enviar → processar → revisar → baixar dentro de uma mesma sessão, mas
não é armazenamento durável (mesmo espírito da retenção curta de 60 minutos
que a aplicação já aplica por padrão). Para um uso de produção com tráfego
real, o recomendado é um host com disco persistente (Render, Fly.io, uma VM
com o `docker-compose.yml` deste repositório, etc.).

## Estrutura do projeto

```
app/
  api/routes.py          # endpoints HTTP (/api/transcricoes, /healthz, ...)
  core/                   # leitura de PDF, OCR, campos (data/hora/dinheiro), incerteza
  extractors/             # um extrator por tipo de documento
  grid.py                 # monta a tabela (avisos incluídos) a partir do JSON
  spreadsheet/writers.py  # gera .xlsx / .csv / .json a partir da grade
  pipeline.py             # fila de processamento em background
  store.py                # persistência dos jobs em disco
  main.py                 # ponto de entrada (FastAPI)
web/
  index.html, app.js, estilo.css   # frontend, sem build
tests/                    # pytest
Dockerfile                # imagem para uso local / docker-compose
Dockerfile.vercel         # imagem para deploy na Vercel (container Function)
vercel.json               # configuração de deploy da Vercel
```

## Licença

Uso livre para fins de estudo e avaliação.
