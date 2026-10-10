from __future__ import annotations

import json

import pytest

from monitoria.julgamento import esquema_json, ler_julgamento
from monitoria.rubrica import Resultado, Rubrica, pontuar


def todos(rubrica: Rubrica, resultado: Resultado) -> dict[str, tuple[Resultado, str]]:
    return {cid: (resultado, "") for cid in rubrica.criterios}


def test_rubrica_soma_100_e_ids_unicos(rubrica):
    assert sum(d.peso for d in rubrica.disciplinas) == 100
    assert len(rubrica.criterios) == 13


def test_tudo_cumprido_vale_100(rubrica):
    nota = pontuar(rubrica, todos(rubrica, Resultado.CUMPRIU), {})
    assert nota.final == 100
    assert all(d.pontos == d.maximo for d in nota.disciplinas)


def test_nada_cumprido_vale_zero(rubrica):
    assert pontuar(rubrica, todos(rubrica, Resultado.NAO_CUMPRIU), {}).final == 0


def test_peso_relativo_dentro_da_disciplina(rubrica):
    # Abertura vale 20: saudação (3), identidade (5), gravação (2). Só a identidade cumprida = 20 * 5/10.
    criterios = todos(rubrica, Resultado.CUMPRIU)
    criterios["saudacao"] = (Resultado.NAO_CUMPRIU, "")
    criterios["aviso_gravacao"] = (Resultado.NAO_CUMPRIU, "")
    nota = pontuar(rubrica, criterios, {})
    abertura = next(d for d in nota.disciplinas if d.disciplina == "abertura")
    assert abertura.pontos == 10.0
    assert nota.final == 90.0


def test_nao_se_aplica_sai_do_calculo(rubrica):
    criterios = todos(rubrica, Resultado.CUMPRIU)
    criterios["recapitulacao"] = (Resultado.NAO_SE_APLICA, "")
    criterios["envio_boleto"] = (Resultado.NAO_SE_APLICA, "")
    nota = pontuar(rubrica, criterios, {})
    assert nota.final == 100, "critério que não se aplica não tira ponto"


def test_disciplina_inteira_sem_aplicacao_e_reescalada(rubrica):
    criterios = todos(rubrica, Resultado.CUMPRIU)
    for cid in ("recapitulacao", "envio_boleto", "despedida"):
        criterios[cid] = (Resultado.NAO_SE_APLICA, "")
    criterios["motivo_atraso"] = (Resultado.NAO_CUMPRIU, "")  # perde 35 * 3/14 = 7,5 de 80 possíveis
    nota = pontuar(rubrica, criterios, {})
    fechamento = next(d for d in nota.disciplinas if d.disciplina == "fechamento")
    assert fechamento.pontos is None
    assert nota.final == pytest.approx((80 - 7.5) / 80 * 100, abs=0.05)


def test_falha_grave_zera_mas_guarda_a_nota_sem_falha(rubrica):
    nota = pontuar(rubrica, todos(rubrica, Resultado.CUMPRIU), {"ameaca_constrangimento": True, "agressividade": False})
    assert nota.final == 0
    assert nota.nota_sem_falha == 100
    assert nota.falhas_graves == ["ameaca_constrangimento"]


def test_classificacao_incompleta_e_recusada(rubrica):
    criterios = todos(rubrica, Resultado.CUMPRIU)
    del criterios["despedida"]
    with pytest.raises(ValueError, match="despedida"):
        pontuar(rubrica, criterios, {})


def resposta_valida(rubrica: Rubrica) -> dict:
    return {
        "criterios": [{"id": cid, "resultado": "cumpriu", "evidencia": "trecho"} for cid in rubrica.criterios],
        "falhas_graves": [{"id": f.id, "ocorreu": False, "evidencia": ""} for f in rubrica.falhas_graves],
        "resumo": "ok",
        "pontos_fortes": [],
        "pontos_de_melhoria": [],
    }


def test_resposta_da_ia_valida_vira_nota(rubrica):
    julgamento = ler_julgamento(json.dumps(resposta_valida(rubrica)), rubrica)
    assert julgamento.nota(rubrica).final == 100


@pytest.mark.parametrize(
    "estragar, mensagem",
    [
        (lambda r: r["criterios"].pop(), "Faltam"),
        (lambda r: r["criterios"].append({"id": "inventado", "resultado": "cumpriu", "evidencia": ""}), "desconhecidos"),
        (lambda r: r["criterios"].append(dict(r["criterios"][0])), "repetido"),
        (lambda r: r["criterios"][0].update(resultado="talvez"), "fora do formato"),
        (lambda r: r["falhas_graves"].pop(), "Falhas graves"),
    ],
)
def test_resposta_da_ia_fora_da_rubrica_e_recusada(rubrica, estragar, mensagem):
    resposta = resposta_valida(rubrica)
    estragar(resposta)
    with pytest.raises(ValueError, match=mensagem):
        ler_julgamento(resposta, rubrica)


def test_texto_que_nao_e_json_e_recusado(rubrica):
    with pytest.raises(ValueError, match="fora do formato"):
        ler_julgamento("Claro! Aqui está a avaliação: nota 9", rubrica)


def test_esquema_strict_restringe_ids_da_rubrica(rubrica):
    esquema = esquema_json(rubrica)
    item = esquema["properties"]["criterios"]["items"]
    assert esquema["additionalProperties"] is False
    assert set(item["properties"]["id"]["enum"]) == set(rubrica.criterios)
    assert item["properties"]["resultado"]["enum"] == ["cumpriu", "nao_cumpriu", "nao_se_aplica"]
    # modo strict: todo campo declarado precisa estar em required
    for objeto in (esquema, item, esquema["properties"]["falhas_graves"]["items"]):
        assert set(objeto["required"]) == set(objeto["properties"])
