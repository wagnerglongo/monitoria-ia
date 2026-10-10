from __future__ import annotations

from datetime import timedelta

import pytest

from monitoria.ia import ErroIA
from monitoria.modelos import Ligacao, Status, agora
from monitoria.pipeline import Pipeline, ligacoes_travadas


def carregar(sessoes, ligacao_id: int) -> Ligacao:
    with sessoes() as sessao:
        ligacao = sessao.get(Ligacao, ligacao_id)
        ligacao.disciplinas, ligacao.criterios
        return ligacao


def test_ligacao_exemplar_tira_100_com_evidencias(pipeline, sessoes, enfileirar):
    ligacao_id = enfileirar("01-juliana-acordo.txt")
    resultado = pipeline.processar_pendentes()

    assert resultado.concluidas == 1
    ligacao = carregar(sessoes, ligacao_id)
    assert ligacao.status is Status.CONCLUIDA
    assert ligacao.nota == 100
    assert len(ligacao.criterios) == 13
    assert all(c.evidencia for c in ligacao.criterios), "toda nota vem com o trecho que a justifica"
    assert ligacao.rubrica_versao == "cobranca@2026.10"


def test_ameaca_zera_a_nota(pipeline, sessoes, enfileirar):
    ligacao_id = enfileirar("04-diego-falha-grave.txt", tipo="sem_acordo")
    pipeline.processar_pendentes()
    ligacao = carregar(sessoes, ligacao_id)
    assert ligacao.falha_grave is True
    assert ligacao.nota == 0
    falhas = {f["id"] for f in ligacao.julgamento["falhas_graves"] if f["ocorreu"]}
    assert "ameaca_constrangimento" in falhas


def test_falar_da_divida_com_terceiro_e_falha_grave(pipeline, sessoes, enfileirar):
    ligacao_id = enfileirar("05-rafael-terceiro.txt", tipo="sem_acordo")
    pipeline.processar_pendentes()
    falhas = {f["id"] for f in carregar(sessoes, ligacao_id).julgamento["falhas_graves"] if f["ocorreu"]}
    assert falhas == {"exposicao_terceiros"}


def test_sem_acordo_nao_cobra_recapitulacao_nem_boleto(pipeline, sessoes, enfileirar):
    ligacao_id = enfileirar("02-rafael-sem-acordo.txt", tipo="sem_acordo")
    pipeline.processar_pendentes()
    resultados = {c.criterio: c.resultado for c in carregar(sessoes, ligacao_id).criterios}
    assert resultados["recapitulacao"] == "nao_se_aplica"
    assert resultados["envio_boleto"] == "nao_se_aplica"


class AvaliadorQueFalha:
    modelo = "teste"

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = 0

    def avaliar(self, rubrica, transcricao, contexto):
        self.chamadas += 1
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


class TranscritorContado:
    def __init__(self, base):
        self.base = base
        self.chamadas = 0

    def transcrever(self, arquivo):
        self.chamadas += 1
        return self.base.transcrever(arquivo)


def test_falha_da_ia_vira_erro_e_e_refeita_sem_transcrever_de_novo(sessoes, rubrica, config, enfileirar, pipeline):
    from monitoria.ia.demo import AvaliadorDemo

    ligacao_id = enfileirar("06-bruna-acordo-boa.txt")
    transcritor = TranscritorContado(pipeline.transcritor)
    avaliador = AvaliadorQueFalha([ErroIA("limite de uso"), "isto não é json"])
    instavel = Pipeline(sessoes, rubrica, transcritor, avaliador, config.pasta_arquivos, max_tentativas=3)

    instavel.processar_pendentes()
    ligacao = carregar(sessoes, ligacao_id)
    assert ligacao.status is Status.ERRO
    assert "limite de uso" in ligacao.erro
    assert ligacao.transcricao, "a transcrição fica salva mesmo com a avaliação falhando"

    # Antes do horário da nova tentativa, a ligação não volta para a fila.
    assert instavel.processar_pendentes().concluidas == 0 and avaliador.chamadas == 1

    with sessoes() as sessao:
        sessao.get(Ligacao, ligacao_id).proxima_tentativa = agora() - timedelta(seconds=1)
        sessao.commit()
    instavel.processar_pendentes()
    assert "fora do formato" in carregar(sessoes, ligacao_id).erro, "resposta inválida da IA não vira nota"

    with sessoes() as sessao:
        sessao.get(Ligacao, ligacao_id).proxima_tentativa = agora() - timedelta(seconds=1)
        sessao.commit()
    avaliador.respostas.append(AvaliadorDemo().avaliar(rubrica, carregar(sessoes, ligacao_id).transcricao, {}))
    instavel.processar_pendentes()

    ligacao = carregar(sessoes, ligacao_id)
    assert ligacao.status is Status.CONCLUIDA
    assert ligacao.tentativas == 3
    assert transcritor.chamadas == 1


def test_desiste_depois_do_maximo_de_tentativas(sessoes, rubrica, config, enfileirar, pipeline):
    ligacao_id = enfileirar("03-bruna-acordo.txt")
    avaliador = AvaliadorQueFalha([ErroIA("fora do ar")] * 5)
    instavel = Pipeline(sessoes, rubrica, pipeline.transcritor, avaliador, config.pasta_arquivos, max_tentativas=2)
    for _ in range(4):
        with sessoes() as sessao:
            sessao.get(Ligacao, ligacao_id).proxima_tentativa = agora() - timedelta(seconds=1)
            sessao.commit()
        instavel.processar_pendentes()
    assert avaliador.chamadas == 2
    assert carregar(sessoes, ligacao_id).status is Status.ERRO


def test_dois_workers_nao_pegam_a_mesma_ligacao(pipeline, enfileirar):
    ligacao_id = enfileirar("01-juliana-acordo.txt")
    assert pipeline._reservar(ligacao_id) is True
    assert pipeline._reservar(ligacao_id) is False


def test_ligacao_presa_em_processamento_volta_para_a_fila(pipeline, sessoes, enfileirar):
    ligacao_id = enfileirar("01-juliana-acordo.txt")
    pipeline._reservar(ligacao_id)
    with sessoes() as sessao:
        sessao.get(Ligacao, ligacao_id).iniciado_em = agora() - timedelta(hours=1)
        sessao.commit()
        assert ligacoes_travadas(sessao) == 1
        sessao.commit()
    assert pipeline.processar_pendentes().concluidas == 1


def test_reprocessar_mantem_a_transcricao(pipeline, sessoes, enfileirar):
    ligacao_id = enfileirar("06-bruna-acordo-boa.txt")
    pipeline.processar_pendentes()
    pipeline.reprocessar(ligacao_id)
    ligacao = carregar(sessoes, ligacao_id)
    assert ligacao.status is Status.PENDENTE
    assert ligacao.transcricao
    assert pipeline.processar_pendentes().concluidas == 1


def test_audio_sem_transcricao_no_modo_demo_explica_o_que_fazer(pipeline, sessoes, config):
    from datetime import date

    config.pasta_arquivos.mkdir(parents=True, exist_ok=True)
    (config.pasta_arquivos / "ligacao.mp3").write_bytes(b"ID3" + b"\x00" * 100)
    with sessoes() as sessao:
        ligacao = Ligacao(arquivo="ligacao.mp3", nome_original="x.mp3", operador="A", contrato="1", tipo="acordo", data_ligacao=date.today())
        sessao.add(ligacao)
        sessao.commit()
        ligacao_id = ligacao.id
    pipeline.processar_pendentes()
    assert "IA_PROVEDOR=openai" in carregar(sessoes, ligacao_id).erro


@pytest.mark.parametrize("arquivo, nota_maxima", [("03-bruna-acordo.txt", 60), ("06-bruna-acordo-boa.txt", 100)])
def test_ligacoes_de_exemplo_ficam_na_faixa_esperada(pipeline, sessoes, enfileirar, arquivo, nota_maxima):
    ligacao_id = enfileirar(arquivo)
    pipeline.processar_pendentes()
    assert carregar(sessoes, ligacao_id).nota <= nota_maxima
