"""Consultas do painel: indicadores, ranking de operadores e média por disciplina."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .modelos import Ligacao, NotaDisciplina, Status


@dataclass
class Indicadores:
    avaliadas: int
    nota_media: float | None
    com_falha_grave: int
    na_fila: int
    com_erro: int

    @property
    def taxa_falha_grave(self) -> float:
        return 0.0 if not self.avaliadas else self.com_falha_grave / self.avaliadas * 100


def indicadores(sessao: Session) -> Indicadores:
    concluida = Ligacao.status == Status.CONCLUIDA
    linha = sessao.execute(
        select(
            func.count().filter(concluida),
            func.avg(Ligacao.nota).filter(concluida),
            func.count().filter(concluida & Ligacao.falha_grave.is_(True)),
            func.count().filter(Ligacao.status.in_([Status.PENDENTE, Status.PROCESSANDO])),
            func.count().filter(Ligacao.status == Status.ERRO),
        )
    ).one()
    return Indicadores(linha[0], None if linha[1] is None else round(float(linha[1]), 1), linha[2], linha[3], linha[4])


@dataclass
class LinhaOperador:
    operador: str
    ligacoes: int
    nota_media: float
    falhas_graves: int
    pior_disciplina: str | None = None


def ranking_operadores(sessao: Session) -> list[LinhaOperador]:
    linhas = sessao.execute(
        select(
            Ligacao.operador,
            func.count(),
            func.avg(Ligacao.nota),
            func.sum(case((Ligacao.falha_grave.is_(True), 1), else_=0)),
        )
        .where(Ligacao.status == Status.CONCLUIDA)
        .group_by(Ligacao.operador)
        .order_by(func.avg(Ligacao.nota).desc())
    ).all()
    ranking = [LinhaOperador(op, n, round(float(media or 0), 1), int(falhas or 0)) for op, n, media, falhas in linhas]

    # Disciplina com menor aproveitamento de cada operador: é onde o feedback deve começar.
    aproveitamento = sessao.execute(
        select(Ligacao.operador, NotaDisciplina.nome, func.avg(NotaDisciplina.pontos / NotaDisciplina.maximo))
        .join(NotaDisciplina, NotaDisciplina.ligacao_id == Ligacao.id)
        .where(Ligacao.status == Status.CONCLUIDA, NotaDisciplina.pontos.is_not(None))
        .group_by(Ligacao.operador, NotaDisciplina.nome)
    ).all()
    pior: dict[str, tuple[float, str]] = {}
    for operador, nome, valor in aproveitamento:
        if operador not in pior or valor < pior[operador][0]:
            pior[operador] = (float(valor), nome)
    for linha in ranking:
        if linha.operador in pior and pior[linha.operador][0] < 0.999:
            linha.pior_disciplina = pior[linha.operador][1]
    return ranking


def media_por_disciplina(sessao: Session) -> list[tuple[str, float]]:
    """Aproveitamento médio (0 a 100%) de cada disciplina, da pior para a melhor."""
    linhas = sessao.execute(
        select(NotaDisciplina.nome, func.avg(NotaDisciplina.pontos / NotaDisciplina.maximo * 100))
        .join(Ligacao, Ligacao.id == NotaDisciplina.ligacao_id)
        .where(Ligacao.status == Status.CONCLUIDA, NotaDisciplina.pontos.is_not(None))
        .group_by(NotaDisciplina.nome)
        .order_by(func.avg(NotaDisciplina.pontos / NotaDisciplina.maximo))
    ).all()
    return [(nome, round(float(valor), 1)) for nome, valor in linhas]


def ultimas(sessao: Session, limite: int = 25, operador: str | None = None) -> list[Ligacao]:
    consulta = select(Ligacao).order_by(Ligacao.criado_em.desc(), Ligacao.id.desc()).limit(limite)
    if operador:
        consulta = consulta.where(Ligacao.operador == operador)
    return list(sessao.scalars(consulta))
