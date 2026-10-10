"""O provedor OpenAI testado com um cliente falso: confere o pedido enviado e o tratamento da resposta, sem rede."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from monitoria.ia import ErroIA
from monitoria.ia.openai_ia import AvaliadorOpenAI, TranscritorOpenAI
from monitoria.julgamento import ler_julgamento


class ClienteFalso:
    def __init__(self, conteudo=None, recusa=None, erro=None, texto="Operador: bom dia"):
        self.pedidos = []
        self.conteudo, self.recusa, self.erro, self.texto = conteudo, recusa, erro, texto
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._criar))
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._transcrever))

    def _criar(self, **pedido):
        self.pedidos.append(pedido)
        if self.erro:
            raise self.erro
        mensagem = SimpleNamespace(content=self.conteudo, refusal=self.recusa)
        return SimpleNamespace(choices=[SimpleNamespace(message=mensagem)])

    def _transcrever(self, **pedido):
        self.pedidos.append(pedido)
        if self.erro:
            raise self.erro
        return SimpleNamespace(text=self.texto)


def resposta(rubrica):
    return json.dumps(
        {
            "criterios": [{"id": c, "resultado": "cumpriu", "evidencia": "x"} for c in rubrica.criterios],
            "falhas_graves": [{"id": f.id, "ocorreu": False, "evidencia": ""} for f in rubrica.falhas_graves],
            "resumo": "Boa ligação.",
            "pontos_fortes": ["Cordial"],
            "pontos_de_melhoria": [],
        }
    )


def test_pedido_usa_schema_strict_e_temperatura_zero(rubrica):
    cliente = ClienteFalso(conteudo=resposta(rubrica))
    bruto = AvaliadorOpenAI(cliente, "modelo-x").avaliar(rubrica, "Operador: bom dia", {"tipo": "acordo"})

    pedido = cliente.pedidos[0]
    assert pedido["model"] == "modelo-x"
    assert pedido["temperature"] == 0
    assert pedido["response_format"]["json_schema"]["strict"] is True
    assert "saudacao" in pedido["messages"][1]["content"]
    assert "Não calcule nota" in pedido["messages"][0]["content"]
    assert ler_julgamento(bruto, rubrica).nota(rubrica).final == 100


def test_recusa_e_falha_de_rede_viram_erro_ia(rubrica):
    with pytest.raises(ErroIA, match="recusou"):
        AvaliadorOpenAI(ClienteFalso(conteudo=None, recusa="não posso"), "m").avaliar(rubrica, "t", {})
    with pytest.raises(ErroIA, match="TimeoutError"):
        AvaliadorOpenAI(ClienteFalso(erro=TimeoutError("lento")), "m").avaliar(rubrica, "t", {})


def test_transcricao_em_portugues(tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"ID3")
    cliente = ClienteFalso(texto="  Operador: bom dia  ")
    assert TranscritorOpenAI(cliente, "whisper-1").transcrever(audio) == "Operador: bom dia"
    assert cliente.pedidos[0]["language"] == "pt"

    with pytest.raises(ErroIA, match="vazia"):
        TranscritorOpenAI(ClienteFalso(texto=""), "whisper-1").transcrever(audio)
