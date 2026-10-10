"""Rubrica de monitoria e cálculo da nota.

A IA só *classifica* cada critério (cumpriu, não cumpriu, não se aplica) e
aponta a evidência na transcrição. A nota é calculada aqui, em código
determinístico e testado: a mesma classificação sempre gera a mesma nota, e
mudar um peso da rubrica não depende de reescrever prompt.

Regras:
- Cada disciplina vale ``peso`` pontos (as disciplinas da rubrica somam 100).
- Dentro da disciplina, cada critério tem um peso relativo. A disciplina recebe
  ``peso * (pesos cumpridos / pesos aplicáveis)``.
- Critério "não se aplica" sai do cálculo (não ajuda nem atrapalha). Se nenhum
  critério da disciplina se aplica, a disciplina sai da nota e as demais são
  reescaladas para 100.
- Qualquer falha grave zera a nota final, como na monitoria tradicional.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Resultado(str, Enum):
    CUMPRIU = "cumpriu"
    NAO_CUMPRIU = "nao_cumpriu"
    NAO_SE_APLICA = "nao_se_aplica"


@dataclass(frozen=True)
class Criterio:
    id: str
    descricao: str
    peso: float
    sinais_demo: tuple[str, ...] = ()


@dataclass(frozen=True)
class Disciplina:
    id: str
    nome: str
    peso: float
    criterios: tuple[Criterio, ...]


@dataclass(frozen=True)
class FalhaGrave:
    id: str
    descricao: str
    sinais_demo: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rubrica:
    id: str
    versao: str
    nome: str
    descricao: str
    disciplinas: tuple[Disciplina, ...]
    falhas_graves: tuple[FalhaGrave, ...]

    @classmethod
    def carregar(cls, caminho: str | Path) -> "Rubrica":
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
        rubrica = cls(
            id=dados["id"],
            versao=dados["versao"],
            nome=dados["nome"],
            descricao=dados.get("descricao", ""),
            disciplinas=tuple(
                Disciplina(
                    id=d["id"],
                    nome=d["nome"],
                    peso=float(d["peso"]),
                    criterios=tuple(
                        Criterio(c["id"], c["descricao"], float(c["peso"]), tuple(c.get("sinais_demo", ())))
                        for c in d["criterios"]
                    ),
                )
                for d in dados["disciplinas"]
            ),
            falhas_graves=tuple(
                FalhaGrave(f["id"], f["descricao"], tuple(f.get("sinais_demo", ()))) for f in dados.get("falhas_graves", ())
            ),
        )
        rubrica.validar()
        return rubrica

    def validar(self) -> None:
        ids = [c.id for d in self.disciplinas for c in d.criterios] + [f.id for f in self.falhas_graves]
        repetidos = {i for i in ids if ids.count(i) > 1}
        if repetidos:
            raise ValueError(f"IDs repetidos na rubrica: {sorted(repetidos)}")
        if any(c.peso <= 0 for d in self.disciplinas for c in d.criterios) or any(d.peso <= 0 for d in self.disciplinas):
            raise ValueError("Pesos da rubrica precisam ser positivos.")

    @property
    def criterios(self) -> dict[str, tuple[Disciplina, Criterio]]:
        return {c.id: (d, c) for d in self.disciplinas for c in d.criterios}


@dataclass
class NotaCriterio:
    disciplina: str
    criterio: str
    peso: float
    resultado: Resultado
    pontos: float
    evidencia: str


@dataclass
class NotaDisciplina:
    disciplina: str
    nome: str
    pontos: float | None  # None = nenhum critério se aplicou
    maximo: float


@dataclass
class Nota:
    final: float
    nota_sem_falha: float
    falhas_graves: list[str]
    disciplinas: list[NotaDisciplina] = field(default_factory=list)
    criterios: list[NotaCriterio] = field(default_factory=list)


def pontuar(rubrica: Rubrica, criterios: dict[str, tuple[Resultado, str]], falhas: dict[str, bool]) -> Nota:
    """Calcula a nota a partir da classificação de cada critério.

    ``criterios`` mapeia id do critério -> (resultado, evidência) e precisa
    cobrir todos os critérios da rubrica; ``falhas`` mapeia id da falha grave
    -> ocorreu.
    """
    faltando = set(rubrica.criterios) - set(criterios)
    if faltando:
        raise ValueError(f"Classificação incompleta, faltam critérios: {sorted(faltando)}")

    notas_criterios: list[NotaCriterio] = []
    notas_disciplinas: list[NotaDisciplina] = []
    obtido = 0.0
    possivel = 0.0

    for disciplina in rubrica.disciplinas:
        aplicavel = 0.0
        cumprido = 0.0
        itens: list[tuple[Criterio, Resultado, str]] = []
        for criterio in disciplina.criterios:
            resultado, evidencia = criterios[criterio.id]
            itens.append((criterio, resultado, evidencia))
            if resultado is not Resultado.NAO_SE_APLICA:
                aplicavel += criterio.peso
                if resultado is Resultado.CUMPRIU:
                    cumprido += criterio.peso

        pontos = None if aplicavel == 0 else disciplina.peso * cumprido / aplicavel
        notas_disciplinas.append(NotaDisciplina(disciplina.id, disciplina.nome, _arredondar(pontos), disciplina.peso))
        if pontos is not None:
            obtido += pontos
            possivel += disciplina.peso

        for criterio, resultado, evidencia in itens:
            valor = 0.0
            if resultado is Resultado.CUMPRIU and aplicavel:
                valor = disciplina.peso * criterio.peso / aplicavel
            notas_criterios.append(
                NotaCriterio(disciplina.id, criterio.id, criterio.peso, resultado, round(valor, 2), evidencia)
            )

    sem_falha = 100.0 if possivel == 0 else obtido / possivel * 100
    ocorridas = sorted(f.id for f in rubrica.falhas_graves if falhas.get(f.id))
    final = 0.0 if ocorridas else sem_falha
    return Nota(round(final, 1), round(sem_falha, 1), ocorridas, notas_disciplinas, notas_criterios)


def _arredondar(valor: float | None) -> float | None:
    return None if valor is None else round(valor, 2)
