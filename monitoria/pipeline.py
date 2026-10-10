"""Processamento das ligações: transcrever, avaliar, validar, pontuar e gravar.

Garantias que o fluxo antigo no n8n não tinha:
- Cada ligação é "reservada" com um UPDATE condicional antes de processar, então
  dois workers rodando juntos nunca avaliam a mesma ligação.
- A transcrição é gravada assim que fica pronta: se a avaliação falhar, a nova
  tentativa não paga a transcrição de novo.
- A resposta da IA é validada contra a rubrica antes de virar nota.
- Falhas viram status "erro" com a mensagem e são refeitas com espera
  crescente, até ``max_tentativas``.
- Tudo é gravado com parâmetros pelo ORM (nada de SQL montado com texto).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from .ia import Avaliador, ErroIA, Transcritor
from .julgamento import ler_julgamento
from .modelos import Ligacao, NotaCriterio, NotaDisciplina, Status, agora
from .rubrica import Rubrica

log = logging.getLogger("monitoria.pipeline")


@dataclass
class ResultadoLote:
    concluidas: int = 0
    com_erro: int = 0


class Pipeline:
    def __init__(
        self,
        sessoes: sessionmaker,
        rubrica: Rubrica,
        transcritor: Transcritor,
        avaliador: Avaliador,
        pasta_arquivos: Path,
        max_tentativas: int = 3,
    ) -> None:
        self.sessoes = sessoes
        self.rubrica = rubrica
        self.transcritor = transcritor
        self.avaliador = avaliador
        self.pasta = pasta_arquivos
        self.max_tentativas = max_tentativas

    def processar_pendentes(self, limite: int = 10) -> ResultadoLote:
        resultado = ResultadoLote()
        for ligacao_id in self._candidatas(limite):
            if not self._reservar(ligacao_id):
                continue  # outro worker pegou antes
            if self.processar(ligacao_id):
                resultado.concluidas += 1
            else:
                resultado.com_erro += 1
        return resultado

    def processar(self, ligacao_id: int) -> bool:
        with self.sessoes() as sessao:
            ligacao = sessao.get(Ligacao, ligacao_id)
            if ligacao is None:
                return False
            try:
                if not ligacao.transcricao:
                    ligacao.transcricao = self.transcritor.transcrever(self.pasta / ligacao.arquivo)
                    sessao.commit()

                bruto = self.avaliador.avaliar(
                    self.rubrica,
                    ligacao.transcricao,
                    {"tipo": ligacao.tipo, "observacoes": ligacao.observacoes},
                )
                julgamento = ler_julgamento(bruto, self.rubrica)
                nota = julgamento.nota(self.rubrica)

                ligacao.disciplinas.clear()
                ligacao.criterios.clear()
                sessao.flush()
                ligacao.disciplinas.extend(
                    NotaDisciplina(disciplina=d.disciplina, nome=d.nome, pontos=d.pontos, maximo=d.maximo) for d in nota.disciplinas
                )
                ligacao.criterios.extend(
                    NotaCriterio(
                        disciplina=c.disciplina,
                        criterio=c.criterio,
                        peso=c.peso,
                        resultado=c.resultado.value,
                        pontos=c.pontos,
                        evidencia=c.evidencia,
                    )
                    for c in nota.criterios
                )
                ligacao.nota = nota.final
                ligacao.nota_sem_falha = nota.nota_sem_falha
                ligacao.falha_grave = bool(nota.falhas_graves)
                ligacao.resumo = julgamento.resumo
                ligacao.julgamento = julgamento.model_dump(mode="json")
                ligacao.rubrica_versao = f"{self.rubrica.id}@{self.rubrica.versao}"
                ligacao.modelo = self.avaliador.modelo
                ligacao.status = Status.CONCLUIDA
                ligacao.erro = None
                ligacao.processado_em = agora()
                sessao.commit()
                log.info("ligação %s avaliada: nota %.1f", ligacao_id, nota.final)
                return True
            except (ErroIA, ValueError, OSError) as erro:
                sessao.rollback()
                self._registrar_erro(ligacao_id, erro)
                return False

    def reprocessar(self, ligacao_id: int) -> None:
        """Volta a ligação para a fila (ex.: depois de mudar a rubrica), mantendo a transcrição."""
        with self.sessoes() as sessao:
            sessao.execute(
                update(Ligacao)
                .where(Ligacao.id == ligacao_id, Ligacao.status != Status.PROCESSANDO)
                .values(status=Status.PENDENTE, tentativas=0, erro=None, proxima_tentativa=None)
            )
            sessao.commit()

    # ---- internos ------------------------------------------------------

    def _candidatas(self, limite: int) -> list[int]:
        with self.sessoes() as sessao:
            consulta = (
                select(Ligacao.id)
                .where(
                    or_(
                        Ligacao.status == Status.PENDENTE,
                        (Ligacao.status == Status.ERRO)
                        & (Ligacao.tentativas < self.max_tentativas)
                        & (Ligacao.proxima_tentativa <= agora()),
                    )
                )
                .order_by(Ligacao.criado_em)
                .limit(limite)
            )
            return list(sessao.scalars(consulta))

    def _reservar(self, ligacao_id: int) -> bool:
        with self.sessoes() as sessao:
            alteradas = sessao.execute(
                update(Ligacao)
                .where(Ligacao.id == ligacao_id, Ligacao.status.in_([Status.PENDENTE, Status.ERRO]))
                .values(status=Status.PROCESSANDO, tentativas=Ligacao.tentativas + 1, iniciado_em=agora())
            ).rowcount
            sessao.commit()
            return alteradas == 1

    def _registrar_erro(self, ligacao_id: int, erro: Exception) -> None:
        with self.sessoes() as sessao:
            ligacao = sessao.get(Ligacao, ligacao_id)
            if ligacao is None:
                return
            espera = timedelta(minutes=2 ** max(0, ligacao.tentativas - 1))
            ligacao.status = Status.ERRO
            ligacao.erro = str(erro)[:1000]
            ligacao.proxima_tentativa = agora() + espera
            sessao.commit()
            log.warning("ligação %s falhou (tentativa %s): %s", ligacao_id, ligacao.tentativas, erro)


def ligacoes_travadas(sessao: Session, minutos: int = 30) -> int:
    """Devolve à fila ligações presas em "processando" (worker morto no meio)."""
    limite = agora() - timedelta(minutes=minutos)
    return sessao.execute(
        update(Ligacao)
        .where(Ligacao.status == Status.PROCESSANDO, Ligacao.iniciado_em < limite)
        .values(status=Status.ERRO, erro="Processamento interrompido; será refeito.", proxima_tentativa=agora())
    ).rowcount
