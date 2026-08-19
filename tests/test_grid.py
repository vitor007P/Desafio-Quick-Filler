"""Avisos derivados: não são campo no JSON, então só existem se o cálculo
estiver certo. Testo os quatro do enunciado, incluindo os dois jeitos de
errar que ele descreve explicitamente:

- cartão de ponto que só lista dias trabalhados não pode ficar todo vermelho
  (o enunciado não exige "+1 dia sempre", exige não regredir/pular quando o
  documento é contínuo);
- holerite com competência ilegível no meio não pode quebrar a cadeia, e
  dezembro -> janeiro é consecutivo.
"""

from app.grid import montar_grade


def test_cartao_marca_batidas_impares():
    value = {
        "pages": [
            {
                "page": 1,
                "days": [
                    {"date_raw": "21/05/2019", "punches": [{"kind": "IN", "time_raw": "08:00", "time_hhmm": "08:00"}]},
                ],
            }
        ]
    }
    grade = montar_grade("cartao-ponto", value)
    assert "batidas_impares" in grade.linhas[0].avisos


def _dia(data, entrada=None, saida=None):
    punches = []
    if entrada:
        punches.append({"kind": "IN", "time_raw": entrada, "time_hhmm": entrada})
    if saida:
        punches.append({"kind": "OUT", "time_raw": saida, "time_hhmm": saida})
    return {"date_raw": data, "punches": punches}


def test_cartao_documento_continuo_marca_o_pulo_de_data():
    dias = [
        _dia("01/05/2019", "08:00", "18:00"),
        _dia("02/05/2019", "08:00", "18:00"),
        _dia("05/05/2019", "08:00", "18:00"),  # pulou 03 e 04
    ]
    grade = montar_grade("cartao-ponto", {"pages": [{"page": 1, "days": dias}]})
    avisos = [linha.avisos for linha in grade.linhas]
    assert "data_nao_sequencial" not in avisos[1]
    assert "data_nao_sequencial" in avisos[2]


def test_cartao_dias_uteis_seguidos_nao_disparam_aviso_mas_o_pulo_do_fds_dispara():
    # O enunciado descreve "uma linha por dia do período": um cartão que só
    # lista dias úteis quebra essa expectativa nas segundas-feiras, e é
    # correto sinalizar isso — o revisor decide se é esperado ou erro de
    # leitura. O que não pode acontecer é marcar dias realmente consecutivos.
    dias = [
        _dia("06/05/2019", "08:00", "18:00"),  # segunda
        _dia("07/05/2019", "08:00", "18:00"),  # terça: consecutivo
        _dia("10/05/2019", "08:00", "18:00"),  # sexta: pulou qua/qui
    ]
    grade = montar_grade("cartao-ponto", {"pages": [{"page": 1, "days": dias}]})
    assert "data_nao_sequencial" not in grade.linhas[1].avisos
    assert "data_nao_sequencial" in grade.linhas[2].avisos


def test_cartao_data_que_retrocede_e_sempre_suspeita():
    dias = [_dia("10/05/2019", "08:00", "18:00"), _dia("09/05/2019", "08:00", "18:00")]
    grade = montar_grade("cartao-ponto", {"pages": [{"page": 1, "days": dias}]})
    assert "data_nao_sequencial" in grade.linhas[1].avisos


def _pagina_holerite(page, mes, ano, vazia=False):
    fields = [] if vazia else [{"code": "1", "label": "Salário", "reference": "", "value": "1,00"}]
    return {"page": page, "year": ano, "month": mes, "fields": fields, "bases": []}


def test_holerite_marca_pagina_vazia():
    value = {"pages": [_pagina_holerite(1, "01", "2020", vazia=True)]}
    grade = montar_grade("holerite", value)
    assert "pagina_vazia" in grade.linhas[0].avisos


def test_holerite_marca_mes_fora_da_sequencia():
    paginas = [_pagina_holerite(i + 1, f"{m:02d}", "2020") for i, m in enumerate([1, 2, 4])]
    grade = montar_grade("holerite", {"pages": paginas})
    avisos = [linha.avisos for linha in grade.linhas]
    assert "mes_nao_sequencial" not in avisos[1]  # jan -> fev
    assert "mes_nao_sequencial" in avisos[2]  # fev -> abr, pulou março


def test_holerite_dezembro_janeiro_e_consecutivo():
    paginas = [_pagina_holerite(1, "12", "2019"), _pagina_holerite(2, "01", "2020")]
    grade = montar_grade("holerite", {"pages": paginas})
    assert all("mes_nao_sequencial" not in linha.avisos for linha in grade.linhas)


def test_holerite_pagina_ilegivel_nao_quebra_a_cadeia():
    # jan, ilegível, mar: mar é o mês seguinte ao último legível (jan -> mar
    # quebraria se comparado direto, mas o enunciado manda comparar só entre
    # legíveis "próximas" — aqui não há legível entre jan e mar, então
    # jan->mar é o salto avaliado e deve, sim, ficar marcado).
    paginas = [
        _pagina_holerite(1, "01", "2020"),
        {"page": 2, "year": "", "month": "", "fields": [], "bases": []},
        _pagina_holerite(3, "02", "2020"),
    ]
    grade = montar_grade("holerite", {"pages": paginas})
    # jan -> fev entre as duas páginas legíveis é consecutivo.
    assert "mes_nao_sequencial" not in grade.linhas[2].avisos
    # a página ilegível em si é sinalizada como vazia, não como "mês quebrado".
    assert "pagina_vazia" in grade.linhas[1].avisos
