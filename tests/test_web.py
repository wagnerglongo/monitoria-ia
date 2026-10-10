from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from monitoria.config import Config
from monitoria.web import criar_app
from tests.conftest import EXEMPLOS


@pytest.fixture
def app(config, sessoes):
    return criar_app(config, sessoes)


@pytest.fixture
def cliente(app):
    return TestClient(app)


def csrf(cliente: TestClient) -> str:
    pagina = cliente.get("/ligacoes/nova").text
    return re.search(r'name="csrf" value="([^"]+)"', pagina).group(1)


def enviar(cliente, nome="01-juliana-acordo.txt", conteudo=None, **campos):
    dados = {"operador": "Juliana Prado", "contrato": "ACM41022871", "tipo": "acordo", "data_ligacao": "2026-10-09", "csrf": csrf(cliente)} | campos
    arquivo = conteudo if conteudo is not None else (EXEMPLOS / "01-juliana-acordo.txt").read_bytes()
    return cliente.post("/ligacoes", data=dados, files={"arquivo": (nome, arquivo, "text/plain")}, follow_redirects=False)


def test_painel_vazio(cliente):
    resposta = cliente.get("/")
    assert resposta.status_code == 200
    assert "Nenhuma ligação avaliada ainda" in resposta.text
    assert "script-src 'self'" in resposta.headers["content-security-policy"]


def test_envio_avaliacao_e_relatorio(cliente, app):
    resposta = enviar(cliente)
    assert resposta.status_code == 303
    detalhe = resposta.headers["location"]
    assert "Na fila para avaliação" in cliente.get(detalhe).text

    app.state.pipeline().processar_pendentes()

    pagina = cliente.get(detalhe).text
    assert "100,0" in pagina
    assert "Tenha um ótimo dia!" in pagina
    assert 'class="fala operador marcada"' in pagina, "evidências marcadas na transcrição"

    pdf = cliente.get(detalhe + "/relatorio.pdf")
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")

    painel = cliente.get("/").text
    assert "Juliana Prado" in painel


def test_envio_sem_csrf_e_recusado(cliente):
    resposta = cliente.post(
        "/ligacoes",
        data={"operador": "A", "contrato": "1", "tipo": "acordo", "data_ligacao": "2026-10-09", "csrf": "outro"},
        files={"arquivo": ("a.txt", b"Operador: oi", "text/plain")},
    )
    assert resposta.status_code == 403


@pytest.mark.parametrize(
    "nome, conteudo, mensagem",
    [
        ("ligacao.exe", b"MZ", "Formato não aceito"),
        ("ligacao.mp3", b"isto nao e um mp3", "não corresponde"),
        ("ligacao.wav", b"ID3 mp3 disfarcado", "não corresponde"),
        ("vazio.txt", b"", "Arquivo vazio"),
    ],
)
def test_arquivo_invalido_volta_para_o_formulario(cliente, config, nome, conteudo, mensagem):
    resposta = enviar(cliente, nome=nome, conteudo=conteudo)
    assert resposta.status_code == 422
    assert mensagem in resposta.text
    assert 'value="Juliana Prado"' in resposta.text, "o formulário mantém o que foi digitado"
    assert not any(config.pasta_arquivos.glob("*")) if config.pasta_arquivos.exists() else True


def test_arquivo_maior_que_o_limite(tmp_path, sessoes):
    config = Config.do_ambiente({"PASTA_DADOS": str(tmp_path), "IA_PROVEDOR": "demo", "TAMANHO_MAXIMO_MB": "1"})
    cliente = TestClient(criar_app(config, sessoes))
    resposta = enviar(cliente, nome="grande.txt", conteudo=b"Operador: a\n" * 200_000)
    assert resposta.status_code == 422
    assert "limite de 1 MB" in resposta.text


def test_api_json(cliente, app):
    resposta = cliente.post(
        "/api/ligacoes",
        data={"operador": "Bruna Lemos", "contrato": "ACM1", "tipo": "acordo", "data_ligacao": "2026-10-08"},
        files={"arquivo": ("ligacao.txt", (EXEMPLOS / "06-bruna-acordo-boa.txt").read_bytes(), "text/plain")},
        headers={"X-Requested-With": "monitoria"},
    )
    assert resposta.status_code == 201
    ligacao_id = resposta.json()["id"]
    assert resposta.json()["status"] == "pendente"

    app.state.pipeline().processar_pendentes()
    detalhe = cliente.get(f"/api/ligacoes/{ligacao_id}").json()
    assert detalhe["status"] == "concluida"
    assert detalhe["nota"] > 90
    assert {d["id"] for d in detalhe["disciplinas"]} == {"abertura", "comunicacao", "negociacao", "fechamento"}
    assert cliente.get("/api/operadores").json()[0]["operador"] == "Bruna Lemos"


def test_api_exige_cabecalho_contra_csrf(cliente):
    resposta = cliente.post(
        "/api/ligacoes",
        data={"operador": "A", "contrato": "1", "tipo": "acordo", "data_ligacao": "2026-10-08"},
        files={"arquivo": ("a.txt", b"Operador: oi", "text/plain")},
    )
    assert resposta.status_code == 400


def test_senha_protege_tudo_menos_a_saude(tmp_path, sessoes):
    config = Config.do_ambiente({"PASTA_DADOS": str(tmp_path), "IA_PROVEDOR": "demo", "MONITORIA_SENHA": "senha-forte"})
    cliente = TestClient(criar_app(config, sessoes))
    assert cliente.get("/saude").status_code == 200
    for rota in ("/", "/ligacoes/nova", "/api/ligacoes"):
        assert cliente.get(rota).status_code == 401
    assert cliente.get("/", auth=("monitor", "errada")).status_code == 401
    assert cliente.get("/", auth=("monitor", "senha-forte")).status_code == 200


def test_ia_de_verdade_sem_senha_nao_sobe(tmp_path):
    config = Config.do_ambiente({"PASTA_DADOS": str(tmp_path), "IA_PROVEDOR": "openai"})
    with pytest.raises(RuntimeError, match="MONITORIA_SENHA"):
        criar_app(config)


def test_ligacao_inexistente(cliente):
    assert cliente.get("/ligacoes/999").status_code == 404
    assert cliente.get("/ligacoes/999/relatorio.pdf").status_code == 404
