// Interface de envio → processamento → revisão → download.
//
// Sem framework de propósito: o desafio pede uma tela de revisão honesta, não
// uma SPA. HTML/JS puro servido pelo próprio backend mantém o Docker num
// container só e o build em zero passos.

// pdf.js: biblioteca usada só para desenhar o PDF na tela (a extração dos
// dados é sempre feita no backend, isto aqui é só visualização).
import * as pdfjsLib from "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.7.76/pdf.min.mjs";
pdfjsLib.GlobalWorkerOptions.workerSrc =
  "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.7.76/pdf.worker.min.mjs";

// Atalho para document.querySelector — evita repetir isso o tempo todo.
const $ = (sel) => document.querySelector(sel);

// As três "telas" da aplicação (só uma fica visível por vez).
const telas = {
  envio: $("#tela-envio"),
  processando: $("#tela-processando"),
  revisao: $("#tela-revisao"),
};

// Troca a tela visível: adiciona a classe "ativa" só na tela pedida.
function mostrarTela(nome) {
  for (const [chave, el] of Object.entries(telas)) {
    el.classList.toggle("ativa", chave === nome);
  }
}

// Texto amigável e cor de cada tipo de aviso (os mesmos códigos que o
// backend usa em app/grid.py).
const AVISO_LABEL = {
  batidas_impares: "Batidas ímpares",
  data_nao_sequencial: "Data não sequencial",
  pagina_vazia: "Página vazia",
  mes_nao_sequencial: "Mês não sequencial",
  incerteza: "Caractere não lido (?)",
};
const AVISO_SEVERIDADE = {
  batidas_impares: "amarelo",
  pagina_vazia: "amarelo",
  incerteza: "amarelo",
  data_nao_sequencial: "vermelho",
  mes_nao_sequencial: "vermelho",
};

// --------------------------------------------------------------------------
// Estado
// --------------------------------------------------------------------------

// Um único objeto guarda tudo que a página "lembra" entre as telas: o job
// atual, os dados baixados da API e os temporizadores em andamento.
const estado = {
  id: null, // id da transcrição atual (vem do POST)
  tipo: null, // "cartao-ponto" ou "holerite"
  value: null, // os dados extraídos, no formato do contrato
  spans: [], // coordenadas de origem de cada valor, para o realce no PDF
  grade: null, // tabela pronta (colunas/linhas/avisos), vinda de /grade
  pdf: null, // documento PDF carregado pelo pdf.js
  paginaAtual: 1,
  temporizadorPolling: null, // setInterval que fica perguntando se já terminou
  temporizadorSalvar: null, // setTimeout que atrasa o salvamento (debounce)
};

// --------------------------------------------------------------------------
// Etapa 1 — envio
// --------------------------------------------------------------------------

// Busca os limites do servidor (tamanho máximo, se OCR está ligado) só para
// mostrar essa informação ao usuário antes de ele enviar o arquivo.
async function carregarInfo() {
  try {
    const info = await (await fetch("/api/info")).json();
    $("#ajuda-arquivo").textContent =
      `PDF de até ${info.max_upload_mb} MB, ${info.max_pages} páginas. ` +
      (info.ocr ? "OCR disponível para páginas escaneadas." : "OCR indisponível no momento.");
  } catch {
    // Tela de envio funciona sem isto — é só informativo.
  }
}

// Quando o usuário envia o formulário: sobe o arquivo e começa o polling.
$("#form-envio").addEventListener("submit", async (ev) => {
  ev.preventDefault(); // evita o recarregamento normal da página
  const erroEl = $("#erro-envio");
  erroEl.hidden = true;

  const arquivo = $("#arquivo").files[0];
  const tipo = $("#tipo").value;
  if (!arquivo) return; // nada selecionado, não faz nada

  // multipart/form-data: é o formato que o endpoint de upload espera.
  const dados = new FormData();
  dados.append("arquivo", arquivo);
  dados.append("tipo", tipo);

  $("#btn-enviar").disabled = true; // evita clique duplo enquanto envia
  try {
    const resp = await fetch("/api/transcricoes", { method: "POST", body: dados });
    const corpo = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(corpo.detail || `Falha no envio (HTTP ${resp.status}).`);
    }

    estado.id = corpo.id;
    estado.tipo = tipo;
    mostrarTela("processando");
    iniciarPolling();
  } catch (erro) {
    erroEl.textContent = erro.message;
    erroEl.hidden = false;
  } finally {
    $("#btn-enviar").disabled = false;
  }
});

// --------------------------------------------------------------------------
// Etapa 2 — processando
// --------------------------------------------------------------------------

// O processamento acontece no servidor em background (ver app/pipeline.py).
// Aqui a tela fica perguntando "já terminou?" a cada 1,5s até a resposta ser
// "concluido" ou "erro".
function iniciarPolling() {
  let tentativas = 0;
  clearInterval(estado.temporizadorPolling); // garante que não haja dois polls ao mesmo tempo
  estado.temporizadorPolling = setInterval(async () => {
    tentativas += 1;
    try {
      const resp = await fetch(`/api/transcricoes/${estado.id}`);
      if (!resp.ok) throw new Error("Transcrição não encontrada.");
      const corpo = await resp.json();

      if (corpo.status === "concluido") {
        clearInterval(estado.temporizadorPolling);
        estado.value = corpo.value;
        await entrarEmRevisao();
      } else if (corpo.status === "erro") {
        clearInterval(estado.temporizadorPolling);
        voltarComErro(corpo.erro || "Falha ao processar o documento.");
      } else {
        // Ainda processando: só atualiza o texto com o tempo decorrido.
        $("#texto-processando").textContent =
          `Processando documento… (${tentativas * 1.5 | 0}s)`;
      }
    } catch (erro) {
      clearInterval(estado.temporizadorPolling);
      voltarComErro(erro.message);
    }
  }, 1500);
}

// Volta para a tela de envio mostrando a mensagem de erro.
function voltarComErro(mensagem) {
  mostrarTela("envio");
  const erroEl = $("#erro-envio");
  erroEl.textContent = mensagem;
  erroEl.hidden = false;
}

// --------------------------------------------------------------------------
// Etapa 3 — revisão
// --------------------------------------------------------------------------

// Busca a tabela pronta (grade) e as coordenadas (spans), monta a tela e
// carrega o PDF original para exibição lado a lado.
async function entrarEmRevisao() {
  const [grade, spans] = await Promise.all([
    (await fetch(`/api/transcricoes/${estado.id}/grade`)).json(),
    (await fetch(`/api/transcricoes/${estado.id}/spans`)).json(),
  ]);
  estado.grade = grade.grade;
  estado.spans = spans.spans;

  mostrarTela("revisao");
  renderizarResumoAvisos();
  renderizarTabela();
  await carregarPdf();
}

// Monta os "chips" no topo da tabela (ex.: "Data não sequencial: 3"),
// contando quantas linhas têm cada tipo de aviso.
function renderizarResumoAvisos() {
  const contagem = {};
  for (const linha of estado.grade.linhas) {
    for (const aviso of linha.avisos) {
      contagem[aviso] = (contagem[aviso] || 0) + 1;
    }
  }

  const alvo = $("#resumo-avisos");
  alvo.innerHTML = "";
  const chaves = Object.keys(contagem);
  if (chaves.length === 0) {
    alvo.innerHTML = `<span class="chip neutro">Nenhum problema destacado</span>`;
    return;
  }
  for (const chave of chaves) {
    const chip = document.createElement("span");
    chip.className = `chip ${AVISO_SEVERIDADE[chave] || "neutro"}`;
    chip.textContent = `${AVISO_LABEL[chave] || chave}: ${contagem[chave]}`;
    alvo.appendChild(chip);
  }
}

// Desenha a tabela inteira (cabeçalho + linhas) a partir de `estado.grade`.
function renderizarTabela() {
  const cabecalho = $("#tabela-cabecalho");
  const corpo = $("#tabela-corpo");
  cabecalho.innerHTML = ""; // limpa antes de redesenhar (usado também depois de salvar)
  corpo.innerHTML = "";

  for (const coluna of estado.grade.colunas) {
    const th = document.createElement("th");
    th.textContent = coluna;
    cabecalho.appendChild(th);
  }

  estado.grade.linhas.forEach((linha, indiceLinha) => {
    const tr = document.createElement("tr");
    if (linha.severidade === "amarelo") tr.classList.add("linha-amarela");
    if (linha.severidade === "vermelho") tr.classList.add("linha-vermelha");
    if (linha.mensagens.length) tr.title = linha.mensagens.join(" · "); // tooltip ao passar o mouse

    linha.celulas.forEach((valor, indiceColuna) => {
      const td = document.createElement("td");
      td.textContent = valor;
      if ((valor || "").includes("?")) td.classList.add("tem-incerteza");

      const editavel = ehEditavel(indiceColuna);
      if (editavel) {
        td.contentEditable = "true"; // permite editar direto na célula
        td.addEventListener("blur", () => confirmarEdicao(td, linha, indiceColuna));
        td.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter") {
            ev.preventDefault(); // Enter não quebra linha na célula, só confirma
            td.blur();
          }
        });
      }
      td.addEventListener("click", () => destacarNoPdf(linha, indiceColuna));

      tr.appendChild(td);
    });

    corpo.appendChild(tr);
  });
}

// Quais colunas o usuário pode editar diretamente na tabela.
function ehEditavel(indiceColuna) {
  if (estado.tipo === "cartao-ponto") return true; // Data e todas as batidas
  return indiceColuna !== 0; // holerite: Pág. não se edita, Mês/Ano/verbas sim
}

// -- edição ------------------------------------------------------------

// Chamado quando o usuário termina de editar uma célula (perde o foco).
function confirmarEdicao(td, linha, indiceColuna) {
  const novoValor = td.textContent.trim();
  td.classList.add("editada"); // marca visualmente que essa célula foi mudada

  if (estado.tipo === "cartao-ponto") {
    aplicarEdicaoCartao(linha.ref, indiceColuna, novoValor);
  } else {
    aplicarEdicaoHolerite(linha.ref, estado.grade.colunas[indiceColuna], indiceColuna, novoValor);
  }

  agendarSalvar();
}

// Aplica a edição de uma célula no objeto `estado.value` (cartão de ponto).
function aplicarEdicaoCartao(ref, indiceColuna, novoValor) {
  const dia = estado.value.pages[ref.page_index].days[ref.day_index];

  if (indiceColuna === 0) {
    dia.date_raw = novoValor; // coluna 0 é sempre a data
    return;
  }

  const indicePunch = indiceColuna - 1; // as colunas seguintes são as batidas, em ordem
  const kind = indicePunch % 2 === 0 ? "IN" : "OUT"; // colunas pares = entrada, ímpares = saída

  // Se o usuário digitou numa coluna além do que o dia já tinha, cria as
  // batidas vazias que faltam no meio, para não deixar buraco na lista.
  while (dia.punches.length <= indicePunch) {
    const k = dia.punches.length % 2 === 0 ? "IN" : "OUT";
    dia.punches.push({ kind: k, time_raw: "", time_hhmm: "" });
  }

  if (novoValor === "") {
    // Só encolhe do fim: apagar uma batida do meio deixaria os pares
    // seguintes com o kind errado, e isso é decisão demais para uma célula.
    if (indicePunch === dia.punches.length - 1) {
      dia.punches.pop();
    } else {
      dia.punches[indicePunch] = { kind, time_raw: "", time_hhmm: "" };
    }
    return;
  }

  dia.punches[indicePunch] = {
    kind,
    time_raw: novoValor,
    time_hhmm: normalizarHoraDigitada(novoValor),
  };
}

// Converte o que o usuário digitou (ex.: "8", "830", "8:3") num "HH:MM" válido.
function normalizarHoraDigitada(texto) {
  let limpo = texto.replace(/[^0-9?:]/g, ""); // remove tudo que não é dígito, "?" ou ":"
  if (!limpo.includes(":")) {
    // Sem ":" digitado: assume que os 2 últimos dígitos são o minuto.
    if (limpo.length <= 2) limpo = limpo.padStart(2, "0") + ":00";
    else limpo = limpo.slice(0, -2).padStart(2, "0") + ":" + limpo.slice(-2);
  }
  let [h, m] = limpo.split(":");
  h = (h || "").padStart(2, "0"); // hora sempre com 2 dígitos
  m = (m || "").padEnd(2, "0").slice(0, 2); // minuto sempre com 2 dígitos
  return `${h}:${m}`;
}

// Aplica a edição de uma célula no objeto `estado.value` (holerite).
function aplicarEdicaoHolerite(ref, rotuloColuna, indiceColuna, novoValor) {
  const pagina = estado.value.pages[ref.page_index];

  if (indiceColuna === 1) {
    pagina.month = novoValor;
    return;
  }
  if (indiceColuna === 2) {
    pagina.year = novoValor;
    return;
  }

  // Demais colunas são verbas: acha o campo pelo rótulo da coluna.
  const correspondentes = pagina.fields.filter((f) => f.label === rotuloColuna);
  if (correspondentes.length === 1) {
    correspondentes[0].value = novoValor;
  } else if (correspondentes.length === 0) {
    pagina.fields.push({ code: "", label: rotuloColuna, reference: "", value: novoValor });
  }
  // Duas verbas com o mesmo rótulo na mesma página: a célula mostra
  // "v1 / v2" e não editamos automaticamente — ambíguo demais para adivinhar
  // qual das duas o usuário quis mudar. Fica para uma revisão futura com uma
  // célula por ocorrência, não por rótulo.
}

// Debounce: espera 700ms sem nova edição antes de salvar de fato, para não
// mandar um PUT a cada tecla digitada.
function agendarSalvar() {
  clearTimeout(estado.temporizadorSalvar);
  $("#rodape-status").textContent = "Editando…";
  estado.temporizadorSalvar = setTimeout(salvarEdicoes, 700);
}

// Manda o `estado.value` inteiro para o backend salvar (PUT) e recarrega a
// grade, para os avisos (ex.: "data não sequencial") ficarem atualizados.
async function salvarEdicoes() {
  $("#rodape-status").textContent = "Salvando…";
  try {
    const resp = await fetch(`/api/transcricoes/${estado.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: estado.value }),
    });
    if (!resp.ok) {
      const corpo = await resp.json().catch(() => ({}));
      throw new Error(corpo.detail || "Falha ao salvar.");
    }

    const grade = await (await fetch(`/api/transcricoes/${estado.id}/grade`)).json();
    estado.grade = grade.grade;
    renderizarResumoAvisos();
    renderizarTabela();
    $("#rodape-status").textContent = "Salvo.";
  } catch (erro) {
    $("#rodape-status").textContent = `Não salvou: ${erro.message}`;
  }
}

// --------------------------------------------------------------------------
// PDF + rastreabilidade visual (bônus)
// --------------------------------------------------------------------------

// Baixa o PDF original e abre com o pdf.js, para desenhar na tela.
async function carregarPdf() {
  const buffer = await (await fetch(`/api/transcricoes/${estado.id}/arquivo`)).arrayBuffer();
  estado.pdf = await pdfjsLib.getDocument({ data: buffer }).promise;
  estado.paginaAtual = 1;
  await renderizarPaginaPdf();
}

// Desenha a página atual do PDF dentro do <canvas>.
async function renderizarPaginaPdf() {
  const pagina = await estado.pdf.getPage(estado.paginaAtual);
  const viewport = pagina.getViewport({ scale: 1.4 }); // zoom fixo de exibição
  const canvas = $("#pdf-canvas");
  const ctx = canvas.getContext("2d");
  canvas.width = viewport.width;
  canvas.height = viewport.height;

  await pagina.render({ canvasContext: ctx, viewport }).promise;
  $("#pdf-pagina-atual").textContent = `Página ${estado.paginaAtual} / ${estado.pdf.numPages}`;
  esconderRealce(); // troca de página esconde o retângulo de realce anterior
}

$("#pdf-anterior").addEventListener("click", async () => {
  if (estado.paginaAtual > 1) {
    estado.paginaAtual -= 1;
    await renderizarPaginaPdf();
  }
});
$("#pdf-proxima").addEventListener("click", async () => {
  if (estado.pdf && estado.paginaAtual < estado.pdf.numPages) {
    estado.paginaAtual += 1;
    await renderizarPaginaPdf();
  }
});

// Descobre o "caminho" (path) da célula clicada, no mesmo formato usado
// pelos spans do backend (ex.: "pages.0.days.2.punches.1").
function caminhoDaCelula(ref, indiceColuna) {
  if (estado.tipo === "cartao-ponto") {
    if (indiceColuna === 0) return `pages.${ref.page_index}.days.${ref.day_index}.date_raw`;
    return `pages.${ref.page_index}.days.${ref.day_index}.punches.${indiceColuna - 1}`;
  }
  if (indiceColuna === 0 || indiceColuna === 1 || indiceColuna === 2) return null; // pág/mês/ano
  const rotulo = estado.grade.colunas[indiceColuna];
  const pagina = estado.value.pages[ref.page_index];
  const indiceField = pagina.fields.findIndex((f) => f.label === rotulo);
  if (indiceField === -1) return null;
  return `pages.${ref.page_index}.fields.${indiceField}`;
}

// Ao clicar numa célula: acha a região correspondente no PDF e destaca.
async function destacarNoPdf(linha, indiceColuna) {
  const caminho = caminhoDaCelula(linha.ref, indiceColuna);
  if (!caminho) return; // célula sem correspondência (ex.: coluna calculada)

  const span = estado.spans.find((s) => s.path === caminho || s.path.startsWith(caminho + "."));
  if (!span) {
    esconderRealce();
    return;
  }

  if (span.page !== estado.paginaAtual) {
    estado.paginaAtual = span.page;
    await renderizarPaginaPdf();
  }
  desenharRealce(span.bbox);
}

const ESCALA_PDF = 1.4; // mesma escala usada em pagina.getViewport({ scale })

// Posiciona o retângulo de destaque (div) por cima do <canvas> do PDF.
function desenharRealce(bbox) {
  const realce = $("#pdf-realce");
  const [x0, y0, x1, y1] = bbox;

  realce.style.left = `${x0 * ESCALA_PDF}px`;
  realce.style.top = `${y0 * ESCALA_PDF}px`;
  realce.style.width = `${Math.max(x1 - x0, 2) * ESCALA_PDF}px`; // largura mínima, pra sempre aparecer
  realce.style.height = `${Math.max(y1 - y0, 2) * ESCALA_PDF}px`;
  realce.hidden = false;
}

function esconderRealce() {
  $("#pdf-realce").hidden = true;
}

// --------------------------------------------------------------------------
// Download e reinício
// --------------------------------------------------------------------------

// Baixa a planilha no formato escolhido, criando um link temporário e
// clicando nele via JavaScript (truque comum para forçar o download).
$("#btn-baixar").addEventListener("click", () => {
  const formato = $("#formato-download").value;
  const link = document.createElement("a");
  link.href = `/api/transcricoes/${estado.id}/planilha?formato=${formato}`;
  link.click();
});

// Botão "Nova transcrição": zera todo o estado e volta para a tela de envio.
$("#btn-nova").addEventListener("click", () => {
  clearInterval(estado.temporizadorPolling);
  clearTimeout(estado.temporizadorSalvar);
  estado.id = null;
  estado.value = null;
  estado.spans = [];
  estado.grade = null;
  estado.pdf = null;
  $("#form-envio").reset();
  $("#erro-envio").hidden = true;
  mostrarTela("envio");
});

// Roda assim que a página carrega, para preencher a mensagem de ajuda do upload.
carregarInfo();
