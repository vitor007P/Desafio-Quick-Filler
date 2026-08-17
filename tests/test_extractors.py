"""Extração ponta a ponta sobre PDFs sintéticos.

Sem os exemplos reais versionados no repositório do desafio, estes documentos
gerados em `tests/helpers.py` são o que garante que o pipeline completo — PDF
→ palavras → colunas por cabeçalho → JSON do contrato — funciona antes de
calibrar contra os PDFs verdadeiros.
"""

from app.core.pdfio import carregar_paginas
from app.extractors.cartao_ponto import ExtratorCartaoPonto
from app.extractors.holerite import ExtratorHolerite
from tests.helpers import pdf_cartao_ponto_simples, pdf_holerite_simples


def test_cartao_ponto_le_datas_na_ordem_do_documento():
    paginas = carregar_paginas(pdf_cartao_ponto_simples())
    value = ExtratorCartaoPonto().extrair(paginas).value

    dias = value["pages"][0]["days"]
    assert [d["date_raw"] for d in dias] == [
        "21/05/2019", "22/05/2019", "23/05/2019", "24/05/2019", "25/05/2019",
    ]


def test_cartao_ponto_dia_sem_batida_fica_com_lista_vazia_nao_some():
    # "Perder linhas em silêncio" é o erro nº1 citado nas instruções: um dia
    # sem marcação continua na saída.
    paginas = carregar_paginas(pdf_cartao_ponto_simples())
    value = ExtratorCartaoPonto().extrair(paginas).value

    dia_23 = value["pages"][0]["days"][2]
    assert dia_23["date_raw"] == "23/05/2019"
    assert dia_23["punches"] == []


def test_cartao_ponto_batida_impar_preserva_a_unica_marcacao():
    paginas = carregar_paginas(pdf_cartao_ponto_simples())
    value = ExtratorCartaoPonto().extrair(paginas).value

    dia_25 = value["pages"][0]["days"][4]
    assert len(dia_25["punches"]) == 1
    assert dia_25["punches"][0] == {"kind": "IN", "time_raw": "08:00", "time_hhmm": "08:00"}


def test_holerite_separa_fields_de_bases():
    # A decisão central do extrator: Base INSS / Total / Valor Líquido não
    # podem contaminar `fields`.
    paginas = carregar_paginas(pdf_holerite_simples())
    value = ExtratorHolerite().extrair(paginas).value
    pagina = value["pages"][0]

    rotulos_fields = {f["label"] for f in pagina["fields"]}
    rotulos_bases = {b["label"] for b in pagina["bases"]}

    assert rotulos_fields == {"Salario Base", "Horas Extras", "INSS"}
    assert rotulos_bases == {"Base INSS", "Total Vencimentos", "Valor Liquido"}
    assert rotulos_fields.isdisjoint(rotulos_bases)


def test_holerite_le_competencia_e_preserva_referencia_e_codigo():
    paginas = carregar_paginas(pdf_holerite_simples())
    value = ExtratorHolerite().extrair(paginas).value
    pagina = value["pages"][0]

    assert pagina["month"] == "01"
    assert pagina["year"] == "2020"

    salario = next(f for f in pagina["fields"] if f["label"] == "Salario Base")
    assert salario["code"] == "0010"
    assert salario["reference"] == "220,00"
    assert salario["value"] == "2.389,77"

    inss = next(f for f in pagina["fields"] if f["label"] == "INSS")
    assert inss["reference"] == ""  # sem referência impressa, string vazia


def test_pagina_sem_texto_e_sem_ocr_nao_derruba_a_extracao(monkeypatch):
    # PDF sem qualquer texto reconhecível e OCR desligado (equivalente a
    # Tesseract indisponível no ambiente): o extrator não levanta, devolve
    # páginas vazias — quem decide "não sei ler isto" é o pipeline, comparando
    # o resultado com uma saída totalmente vazia (ver test_pipeline.py).
    import dataclasses

    import app.core.pdfio as pdfio_module

    # `Settings` é frozen — em vez de mutar o singleton, troca a referência
    # que `pdfio` enxerga por uma cópia com OCR desligado.
    monkeypatch.setattr(pdfio_module, "settings", dataclasses.replace(pdfio_module.settings, ocr_enabled=False))
    from tests.helpers import construir_pdf

    conteudo = construir_pdf([[]])
    paginas = carregar_paginas(conteudo)
    value = ExtratorCartaoPonto().extrair(paginas).value
    assert value["pages"][0]["days"] == []
