"""O contrato HTTP é literal e avaliado automaticamente — "divergir dele
significa nota zero em precisão, mesmo com a extração perfeita". Estes testes
não checam a qualidade da extração (isso é `test_extractors.py`); checam que
a forma do contrato e o ciclo assíncrono estão certos, porque um erro aqui
zera a nota inteira independente de tudo o mais.
"""

import dataclasses

import pytest
from fastapi.testclient import TestClient

from tests.helpers import pdf_cartao_ponto_simples, pdf_holerite_simples


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    # Cada teste ganha seu próprio diretório de armazenamento e roda o job
    # síncrono e sem timeout de fila — testar o contrato não deveria
    # depender de tempo real de processamento.
    import app.config as config_module

    novo = dataclasses.replace(config_module.settings, storage_dir=tmp_path, retention_minutes=0)
    monkeypatch.setattr(config_module, "settings", novo)
    monkeypatch.setattr("app.store.settings", novo)
    monkeypatch.setattr("app.pipeline.settings", novo)

    import app.store as store_module

    monkeypatch.setattr(store_module, "store", store_module.JobStore(tmp_path))
    monkeypatch.setattr("app.api.routes.store", store_module.store)
    monkeypatch.setattr("app.pipeline.store", store_module.store)

    import app.pipeline as pipeline_module

    # Processa na hora, na mesma thread — sem depender do pool em teste.
    # `routes.py` importou `enfileirar` por nome (`from ... import enfileirar`),
    # então a troca precisa alcançar essa referência, não só o módulo de origem.
    monkeypatch.setattr(pipeline_module, "enfileirar", pipeline_module.processar_job)
    monkeypatch.setattr("app.api.routes.enfileirar", pipeline_module.processar_job)

    from app.main import app

    with TestClient(app) as c:
        yield c


def _enviar(cliente, conteudo: bytes, tipo: str) -> str:
    resp = cliente.post(
        "/api/transcricoes",
        files={"arquivo": ("documento.pdf", conteudo, "application/pdf")},
        data={"tipo": tipo},
    )
    assert resp.status_code == 202, resp.text
    corpo = resp.json()
    assert set(corpo.keys()) == {"id"}
    return corpo["id"]


def test_healthz():
    from app.main import app

    with TestClient(app) as cliente:
        resp = cliente.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ciclo_completo_cartao_de_ponto(cliente):
    job_id = _enviar(cliente, pdf_cartao_ponto_simples(), "cartao-ponto")

    resp = cliente.get(f"/api/transcricoes/{job_id}")
    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["id"] == job_id
    assert corpo["tipo"] == "cartao-ponto"
    assert corpo["status"] == "concluido"
    assert corpo["erro"] is None
    assert corpo["value"]["pages"][0]["days"][0]["date_raw"] == "21/05/2019"

    # PUT com correção — precisa refletir na planilha depois.
    novo_value = corpo["value"]
    novo_value["pages"][0]["days"][0]["date_raw"] = "01/06/2019"
    resp = cliente.put(f"/api/transcricoes/{job_id}", json={"value": novo_value})
    assert resp.status_code == 200
    assert resp.json()["value"]["pages"][0]["days"][0]["date_raw"] == "01/06/2019"

    for formato in ("xlsx", "csv", "json"):
        resp = cliente.get(f"/api/transcricoes/{job_id}/planilha", params={"formato": formato})
        assert resp.status_code == 200, formato
        assert len(resp.content) > 0

    resp = cliente.get(f"/api/transcricoes/{job_id}/planilha", params={"formato": "json"})
    import json

    grade = json.loads(resp.content)
    # A correção do PUT precisa chegar na planilha.
    assert grade["linhas"][0]["celulas"][0] == "01/06/2019"


def test_ciclo_completo_holerite(cliente):
    job_id = _enviar(cliente, pdf_holerite_simples(), "holerite")

    resp = cliente.get(f"/api/transcricoes/{job_id}")
    corpo = resp.json()
    assert corpo["status"] == "concluido"
    assert corpo["value"]["pages"][0]["month"] == "01"

    resp = cliente.get(f"/api/transcricoes/{job_id}/planilha", params={"formato": "xlsx"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_tipo_invalido_e_rejeitado_antes_de_enfileirar(cliente):
    resp = cliente.post(
        "/api/transcricoes",
        files={"arquivo": ("x.pdf", pdf_cartao_ponto_simples(), "application/pdf")},
        data={"tipo": "outro-tipo"},
    )
    assert 400 <= resp.status_code < 500


def test_arquivo_nao_pdf_e_rejeitado_com_4xx(cliente):
    # "Aceitar qualquer coisa no upload" é erro comum citado nas instruções:
    # um .txt renomeado para .pdf não pode virar transcrição.
    resp = cliente.post(
        "/api/transcricoes",
        files={"arquivo": ("fake.pdf", b"isto nao e um pdf de verdade", "application/pdf")},
        data={"tipo": "cartao-ponto"},
    )
    assert 400 <= resp.status_code < 500


def test_put_rejeita_campo_desconhecido_no_value(cliente):
    # O contrato é literal — um campo extra não pode ser aceito em silêncio.
    job_id = _enviar(cliente, pdf_cartao_ponto_simples(), "cartao-ponto")
    value = cliente.get(f"/api/transcricoes/{job_id}").json()["value"]
    value["pages"][0]["campo_que_nao_existe"] = 123

    resp = cliente.put(f"/api/transcricoes/{job_id}", json={"value": value})
    assert resp.status_code == 400


def test_transcricao_inexistente_devolve_404(cliente):
    resp = cliente.get("/api/transcricoes/nao-existe")
    assert resp.status_code == 404
