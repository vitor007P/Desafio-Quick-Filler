"""A regra que mais pesa na nota: nunca inventar, nunca produzir data impossível.

Cada teste aqui corresponde a uma frase literal do enunciado — é por isso que
existem, e é o motivo de cada um estar aqui e não em outro lugar.
"""

from app.core.fields import sanear_data, sanear_hora, sanear_moeda


def test_data_com_dia_impossivel_marca_so_o_digito_culpado():
    # "38/07" -> nenhum "3X" é dia válido, mas ambos os dígitos poderiam ser
    # o erro (30..39 vs 08/18/28/38): sem um único culpado, os dois viram "?".
    assert sanear_data("38/07/2019") == "??/07/2019"


def test_data_com_um_unico_digito_culpado_preserva_o_outro():
    # "45" só vira válido trocando o primeiro dígito (05..09, 15..19, ...
    # não existe "4X" válido) -> o "5" não é suspeito, só o "4".
    assert sanear_data("45/07/2019") == "?5/07/2019"


def test_data_valida_nao_e_alterada():
    assert sanear_data("21/05/2019") == "21/05/2019"


def test_data_31_de_fevereiro_marca_o_dia_nao_o_mes():
    # Fevereiro é o componente estável do documento inteiro; o dia é o que
    # varia linha a linha, então é ele quem carrega a suspeita.
    assert sanear_data("31/02/2019") == "?1/02/2019"


def test_hora_com_caractere_ilegivel_permanece_marcada_nos_dois_campos():
    raw, hhmm = sanear_hora("0?:25")
    assert raw == "0?:25"
    assert hhmm == "0?:25"


def test_hora_fora_de_faixa_marca_o_digito_impossivel():
    # "38:25" não é hora válida (hora <= 23); só o primeiro dígito da hora
    # explica o erro.
    raw, _hhmm = sanear_hora("38:25")
    assert "?" in raw
    assert raw.endswith(":25")


def test_moeda_nunca_vira_float_mantem_formato_brasileiro():
    valor = sanear_moeda("2.389,77")
    assert valor == "2.389,77"
    assert isinstance(valor, str)


def test_moeda_com_caractere_fora_do_charset_vira_interrogacao():
    assert sanear_moeda("2.3X9,77") == "2.3?9,77"
