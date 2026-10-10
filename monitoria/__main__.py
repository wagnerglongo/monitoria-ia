"""Linha de comando.

    python -m monitoria web       # painel e API em http://localhost:8000
    python -m monitoria worker    # processa a fila continuamente
    python -m monitoria processar # processa o que está na fila agora e sai
    python -m monitoria demo      # carrega as ligações de exemplo e avalia
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import signal
import time
from datetime import date, timedelta
from pathlib import Path

from .config import RAIZ, Config
from .ia import criar_provedores
from .modelos import Ligacao, criar_motor, fabrica_sessoes
from .pipeline import Pipeline, ligacoes_travadas
from .rubrica import Rubrica

log = logging.getLogger("monitoria")


def montar_pipeline(config: Config) -> Pipeline:
    sessoes = fabrica_sessoes(criar_motor(config.banco_url))
    transcritor, avaliador = criar_provedores(
        config.ia_provedor,
        api_key=config.openai_api_key,
        modelo_transcricao=config.modelo_transcricao,
        modelo_avaliacao=config.modelo_avaliacao,
    )
    return Pipeline(sessoes, Rubrica.carregar(config.rubrica), transcritor, avaliador, config.pasta_arquivos, config.max_tentativas)


def worker(config: Config) -> None:
    pipeline = montar_pipeline(config)
    rodando = True

    def parar(*_: object) -> None:
        nonlocal rodando
        rodando = False
        log.info("encerrando depois do item atual...")

    signal.signal(signal.SIGTERM, parar)
    signal.signal(signal.SIGINT, parar)
    log.info("worker iniciado (provedor %s, intervalo %ss)", config.ia_provedor, config.intervalo_worker)
    ultima_faxina = 0.0
    while rodando:
        if time.monotonic() - ultima_faxina > 300:
            with pipeline.sessoes() as sessao:
                if devolvidas := ligacoes_travadas(sessao):
                    log.warning("%s ligação(ões) presas em processamento voltaram para a fila", devolvidas)
                sessao.commit()
            ultima_faxina = time.monotonic()
        lote = pipeline.processar_pendentes()
        if lote.concluidas or lote.com_erro:
            log.info("lote: %s avaliada(s), %s com erro", lote.concluidas, lote.com_erro)
        else:
            time.sleep(config.intervalo_worker)


def demo(config: Config) -> None:
    """Copia as transcrições de exemplo para a pasta de arquivos, enfileira e avalia."""
    pipeline = montar_pipeline(config)
    exemplos = RAIZ / "exemplos"
    config.pasta_arquivos.mkdir(parents=True, exist_ok=True)
    criadas = 0
    with pipeline.sessoes() as sessao:
        for item in json.loads((exemplos / "ligacoes.json").read_text(encoding="utf-8")):
            nome = "exemplo-" + item["arquivo"]
            if sessao.query(Ligacao).filter_by(arquivo=nome).first():
                continue
            shutil.copyfile(exemplos / "ligacoes" / item["arquivo"], config.pasta_arquivos / nome)
            sessao.add(
                Ligacao(
                    arquivo=nome,
                    nome_original=item["arquivo"],
                    operador=item["operador"],
                    contrato=item["contrato"],
                    tipo=item["tipo"],
                    monitor="Demonstração",
                    data_ligacao=date.today() - timedelta(days=item["dias_atras"]),
                )
            )
            criadas += 1
        sessao.commit()
    lote = pipeline.processar_pendentes(limite=100)
    print(f"{criadas} ligação(ões) de exemplo enfileirada(s); {lote.concluidas} avaliada(s), {lote.com_erro} com erro.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="monitoria", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("comando", choices=["web", "worker", "processar", "demo"])
    parser.add_argument("--porta", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    argumentos = parser.parse_args()
    config = Config.do_ambiente()

    if argumentos.comando == "web":
        import uvicorn

        from .web import criar_app

        uvicorn.run(criar_app(config), host=argumentos.host, port=argumentos.porta, proxy_headers=True)
    elif argumentos.comando == "worker":
        worker(config)
    elif argumentos.comando == "processar":
        lote = montar_pipeline(config).processar_pendentes(limite=1000)
        print(f"{lote.concluidas} avaliada(s), {lote.com_erro} com erro.")
    else:
        demo(config)


if __name__ == "__main__":
    main()
