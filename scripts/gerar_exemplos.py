"""Gera PDFs de exemplo em `exemplos/` para testar a aplicação manualmente.

Não são documentos reais (nenhum dado de pessoa real) — são construídos com
PyMuPDF do mesmo jeito que `tests/helpers.py` monta os PDFs sintéticos dos
testes automatizados, só que mais completos: um mês inteiro de cartão de
ponto, um holerite com várias verbas, um holerite de duas páginas com uma
competência fora de sequência, e uma versão "escaneada" (sem camada de texto)
para exercitar o caminho de OCR.

Rodar com o ambiente virtual do projeto ativado:

    python scripts/gerar_exemplos.py
"""

from __future__ import annotations

import calendar
from pathlib import Path

import fitz

RAIZ = Path(__file__).resolve().parent.parent
SAIDA = RAIZ / "exemplos"


def construir_pdf(paginas: list[list[tuple[float, float, str]]]) -> bytes:
    """Cada página é uma lista de (x, y, texto) em pontos, topo-esquerda."""
    doc = fitz.open()
    for itens in paginas:
        page = doc.new_page(width=595, height=842)
        for x, y, texto in itens:
            page.insert_text((x, y), texto, fontsize=10)
    conteudo = doc.tobytes()
    doc.close()
    return conteudo


# --------------------------------------------------------------------------
# Cartão de ponto — maio/2024, um mês inteiro de dias úteis
# --------------------------------------------------------------------------

X_DATA, X_IN1, X_OUT1, X_IN2, X_OUT2, X_RESUMO = 45, 140, 210, 280, 350, 430


def _pagina_cartao_ponto() -> list[tuple[float, float, str]]:
    itens: list[tuple[float, float, str]] = [
        (45, 25, "Empresa: Comércio de Materiais Ltda    CNPJ: 12.345.678/0001-90"),
        (45, 40, "Funcionário: João da Silva Pereira    Matrícula: 4521    Cargo: Auxiliar Administrativo"),
        (45, 55, "Período: 01/05/2024 a 31/05/2024"),
        (X_DATA, 80, "Data"),
        (X_IN1, 80, "Entrada"),
        (X_OUT1, 80, "Saída"),
        (X_IN2, 80, "Entrada"),
        (X_OUT2, 80, "Saída"),
        (X_RESUMO, 80, "Horas Trabalhadas"),
    ]

    # Casos especiais, para exercitar os avisos calculados em app/grid.py.
    SEM_SAIDA_ALMOCO = 10  # só bate a entrada da manhã: batida ímpar (1)
    SEM_ULTIMA_SAIDA = 17  # esquece de bater a saída final: batida ímpar (3)
    FALTA = 23  # nenhuma batida no dia (atestado, folga não registrada, etc.)

    y = 100
    for dia in range(1, 32):
        dia_semana = calendar.weekday(2024, 5, dia)  # 0=segunda ... 6=domingo
        if dia_semana >= 5:
            continue  # fim de semana não entra no cartão (só dias úteis)

        data_str = f"{dia:02d}/05/2024"
        itens.append((X_DATA, y, data_str))

        if dia == FALTA:
            pass  # nenhuma batida — dia fica com a lista de horários vazia
        elif dia == SEM_SAIDA_ALMOCO:
            itens.append((X_IN1, y, "08:05"))
        elif dia == SEM_ULTIMA_SAIDA:
            itens.append((X_IN1, y, "08:00"))
            itens.append((X_OUT1, y, "12:00"))
            itens.append((X_IN2, y, "13:05"))
        else:
            itens.append((X_IN1, y, f"08:0{dia % 6}"))
            itens.append((X_OUT1, y, "12:00"))
            itens.append((X_IN2, y, "13:00"))
            itens.append((X_OUT2, y, f"18:0{dia % 6}"))
            itens.append((X_RESUMO, y, "08:5" + str((6 - dia % 6) % 6)))

        y += 18

    return itens


def gerar_cartao_ponto() -> bytes:
    return construir_pdf([_pagina_cartao_ponto()])


def gerar_cartao_ponto_escaneado() -> bytes:
    """Mesmo conteúdo, mas como imagem — sem camada de texto, força OCR.

    Renderiza a página de texto normal, rasteriza em imagem e monta um PDF
    novo só com a imagem. `app/core/pdfio.py` detecta a baixa densidade de
    texto extraível e cai para o Tesseract, como aconteceria com um cartão
    de ponto escaneado de verdade.
    """
    doc_texto = fitz.open()
    page = doc_texto.new_page(width=595, height=842)
    for x, y, texto in _pagina_cartao_ponto():
        page.insert_text((x, y), texto, fontsize=10)
    pix = page.get_pixmap(dpi=200)
    jpeg = pix.pil_tobytes(format="JPEG", optimize=True, quality=80)
    doc_texto.close()

    doc_imagem = fitz.open()
    pagina_img = doc_imagem.new_page(width=595, height=842)
    pagina_img.insert_image(pagina_img.rect, stream=jpeg)
    conteudo = doc_imagem.tobytes(deflate=True)
    doc_imagem.close()
    return conteudo


# --------------------------------------------------------------------------
# Holerite — uma competência, várias verbas e bases
# --------------------------------------------------------------------------

X_COD, X_DESC, X_REF, X_VENC, X_DESCV = 45, 100, 330, 420, 500


def _pagina_holerite(competencia: str) -> list[tuple[float, float, str]]:
    itens: list[tuple[float, float, str]] = [
        (45, 25, "Empresa: Comércio de Materiais Ltda    CNPJ: 12.345.678/0001-90"),
        (45, 40, "Funcionário: João da Silva Pereira    Matrícula: 4521    CPF: 123.456.789-00"),
        (45, 55, f"Competência: {competencia}"),
        (X_COD, 80, "Código"),
        (X_DESC, 80, "Descrição"),
        (X_REF, 80, "Referência"),
        (X_VENC, 80, "Vencimentos"),
        (X_DESCV, 80, "Descontos"),
    ]

    verbas = [
        ("0010", "Salário Base", "30,00", "4.500,00", None),
        ("0050", "Horas Extras 50%", "6,00", "187,50", None),
        ("0060", "Adicional Noturno 20%", "10,00", "90,00", None),
        ("0100", "DSR", "1,00", "150,00", None),
        ("0150", "Insalubridade", "20%", "220,00", None),
        ("0900", "INSS", None, None, "495,03"),
        ("0950", "IRRF", None, None, "320,15"),
        ("0980", "Vale Transporte", None, None, "108,00"),
        ("0990", "Vale Refeição", None, None, "220,00"),
    ]

    y = 100
    for codigo, descricao, referencia, vencimento, desconto in verbas:
        itens.append((X_COD, y, codigo))
        itens.append((X_DESC, y, descricao))
        if referencia:
            itens.append((X_REF, y, referencia))
        if vencimento:
            itens.append((X_VENC, y, vencimento))
        if desconto:
            itens.append((X_DESCV, y, desconto))
        y += 18

    y += 20
    bases = [
        ("Base INSS", "5.147,50"),
        ("Base FGTS", "5.147,50"),
        ("Base IRRF", "4.652,47"),
        ("FGTS do Mês", "411,80"),
        ("Total de Vencimentos", "5.147,50"),
        ("Total de Descontos", "1.143,18"),
        ("Valor Líquido", "4.004,32"),
    ]
    for rotulo, valor in bases:
        itens.append((X_COD, y, rotulo))
        itens.append((X_VENC, y, valor))
        y += 18

    return itens


def gerar_holerite() -> bytes:
    return construir_pdf([_pagina_holerite("09/2024")])


def gerar_holerite_2_paginas_mes_pulado() -> bytes:
    """01/2024 seguido de 03/2024 — pula fevereiro, aciona o aviso de
    competência fora de sequência (app/grid.py::_meses_nao_sequenciais)."""
    return construir_pdf(
        [_pagina_holerite("01/2024"), _pagina_holerite("03/2024")]
    )


# --------------------------------------------------------------------------


def main() -> None:
    SAIDA.mkdir(exist_ok=True)

    arquivos = {
        "cartao_ponto_maio_2024.pdf": gerar_cartao_ponto(),
        "cartao_ponto_escaneado.pdf": gerar_cartao_ponto_escaneado(),
        "holerite_setembro_2024.pdf": gerar_holerite(),
        "holerite_2_paginas_mes_pulado.pdf": gerar_holerite_2_paginas_mes_pulado(),
    }
    for nome, conteudo in arquivos.items():
        caminho = SAIDA / nome
        caminho.write_bytes(conteudo)
        print(f"gerado: {caminho} ({len(conteudo)} bytes)")


if __name__ == "__main__":
    main()
