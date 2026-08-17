"""Construção de PDFs sintéticos para teste.

Sem os exemplos reais da Quick Filler versionados no repositório do desafio,
os testes de extração validam contra documentos gerados aqui — controlados o
bastante para isolar cada heurística, mas com o mesmo formato de linhas e
colunas de um cartão de ponto ou holerite real.
"""

from __future__ import annotations

import fitz


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


def linha_cartao_ponto(y: float, cabecalho: bool, celulas: list[tuple[float, str]]):
    return [(x, y, texto) for x, texto in celulas]


def pdf_cartao_ponto_simples() -> bytes:
    """01 a 05/05/2019, uma jornada normal, o dia 03 sem batida."""
    itens = [
        (50, 40, "Data"), (150, 40, "Entrada"), (250, 40, "Saida"),
        (50, 60, "21/05/2019"), (150, 60, "08:25"), (250, 60, "18:25"),
        (50, 80, "22/05/2019"), (150, 80, "08:30"), (250, 80, "18:00"),
        (50, 100, "23/05/2019"),
        (50, 120, "24/05/2019"), (150, 120, "08:15"), (250, 120, "18:10"),
        (50, 140, "25/05/2019"), (150, 140, "08:00"),  # batida ímpar
    ]
    return construir_pdf([itens])


def pdf_holerite_simples() -> bytes:
    itens = [
        (50, 40, "Codigo"), (100, 40, "Descricao"), (300, 40, "Referencia"), (400, 40, "Vencimentos"),
        (50, 60, "0010"), (100, 60, "Salario Base"), (300, 60, "220,00"), (400, 60, "2.389,77"),
        (50, 80, "5560"), (100, 80, "Horas Extras"), (300, 80, "8,00"), (400, 80, "155,91"),
        (50, 100, "0998"), (100, 100, "INSS"), (400, 100, "262,87"),
        (50, 140, "Base INSS"), (400, 140, "2.545,68"),
        (50, 160, "Total Vencimentos"), (400, 160, "2.545,68"),
        (50, 180, "Valor Liquido"), (400, 180, "2.282,81"),
        (50, 20, "Competencia: 01/2020"),
    ]
    return construir_pdf([itens])
