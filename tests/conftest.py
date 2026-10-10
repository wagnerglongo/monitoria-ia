from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from monitoria.config import RAIZ, Config
from monitoria.ia.demo import AvaliadorDemo, TranscritorDemo
from monitoria.modelos import Ligacao, criar_motor, fabrica_sessoes
from monitoria.pipeline import Pipeline
from monitoria.rubrica import Rubrica

EXEMPLOS = RAIZ / "exemplos" / "ligacoes"


@pytest.fixture
def rubrica() -> Rubrica:
    return Rubrica.carregar(RAIZ / "rubricas" / "cobranca.json")


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config.do_ambiente({"PASTA_DADOS": str(tmp_path), "IA_PROVEDOR": "demo"})


@pytest.fixture
def sessoes(config: Config):
    return fabrica_sessoes(criar_motor(config.banco_url))


@pytest.fixture
def pipeline(sessoes, rubrica, config) -> Pipeline:
    return Pipeline(sessoes, rubrica, TranscritorDemo(), AvaliadorDemo(), config.pasta_arquivos)


@pytest.fixture
def enfileirar(sessoes, config):
    """Copia uma transcrição de exemplo para a pasta de arquivos e cria a ligação na fila."""

    def _enfileirar(exemplo: str, tipo: str = "acordo", operador: str = "Operador Teste") -> int:
        config.pasta_arquivos.mkdir(parents=True, exist_ok=True)
        (config.pasta_arquivos / exemplo).write_bytes((EXEMPLOS / exemplo).read_bytes())
        with sessoes() as sessao:
            ligacao = Ligacao(
                arquivo=exemplo,
                nome_original=exemplo,
                operador=operador,
                contrato="ACM00000001",
                tipo=tipo,
                data_ligacao=date(2026, 10, 9),
            )
            sessao.add(ligacao)
            sessao.commit()
            return ligacao.id

    return _enfileirar
