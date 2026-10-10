"""Formato da resposta da IA e sua validação.

A resposta do modelo nunca é gravada às cegas: ela passa por este modelo
Pydantic, que exige exatamente um item para cada critério e falha grave da
rubrica. Se o modelo inventar um critério, esquecer outro ou devolver texto
fora do formato, a avaliação vai para "erro" e é refeita, em vez de virar
uma nota errada no banco.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from .rubrica import Nota, Resultado, Rubrica, pontuar


class ItemCriterio(BaseModel):
    id: str
    resultado: Resultado
    evidencia: str = Field(default="", max_length=2000)


class ItemFalha(BaseModel):
    id: str
    ocorreu: bool
    evidencia: str = Field(default="", max_length=2000)


class Julgamento(BaseModel):
    criterios: list[ItemCriterio]
    falhas_graves: list[ItemFalha] = []
    resumo: str = Field(default="", max_length=3000)
    pontos_fortes: list[str] = []
    pontos_de_melhoria: list[str] = []

    @model_validator(mode="after")
    def _sem_repeticao(self) -> "Julgamento":
        for nome, itens in (("critério", self.criterios), ("falha grave", self.falhas_graves)):
            ids = [i.id for i in itens]
            repetidos = {i for i in ids if ids.count(i) > 1}
            if repetidos:
                raise ValueError(f"{nome} repetido na resposta: {sorted(repetidos)}")
        return self

    def conferir(self, rubrica: Rubrica) -> None:
        esperados = set(rubrica.criterios)
        recebidos = {c.id for c in self.criterios}
        if esperados != recebidos:
            raise ValueError(
                f"Critérios não batem com a rubrica. Faltam: {sorted(esperados - recebidos)}; "
                f"desconhecidos: {sorted(recebidos - esperados)}"
            )
        falhas_esperadas = {f.id for f in rubrica.falhas_graves}
        falhas_recebidas = {f.id for f in self.falhas_graves}
        if falhas_esperadas != falhas_recebidas:
            raise ValueError(
                f"Falhas graves não batem com a rubrica. Faltam: {sorted(falhas_esperadas - falhas_recebidas)}; "
                f"desconhecidas: {sorted(falhas_recebidas - falhas_esperadas)}"
            )

    def nota(self, rubrica: Rubrica) -> Nota:
        self.conferir(rubrica)
        return pontuar(
            rubrica,
            {c.id: (c.resultado, c.evidencia) for c in self.criterios},
            {f.id: f.ocorreu for f in self.falhas_graves},
        )


def ler_julgamento(bruto: str | dict[str, Any], rubrica: Rubrica) -> Julgamento:
    """Valida a resposta crua do modelo (JSON em texto ou dict)."""
    try:
        julgamento = (
            Julgamento.model_validate_json(bruto) if isinstance(bruto, str) else Julgamento.model_validate(bruto)
        )
    except ValidationError as erro:
        raise ValueError(f"Resposta da IA fora do formato: {erro.error_count()} problema(s): {erro.errors()[0]['msg']}") from erro
    julgamento.conferir(rubrica)
    return julgamento


def esquema_json(rubrica: Rubrica) -> dict[str, Any]:
    """JSON Schema (modo strict) que restringe os ids aos da rubrica.

    Com saída estruturada, o próprio provedor recusa respostas fora deste
    formato. O schema fica no subconjunto aceito pelo modo strict (sem
    minItems/maxItems), então a cobertura de todos os critérios é conferida
    na validação acima.
    """
    ids_criterios = list(rubrica.criterios)
    ids_falhas = [f.id for f in rubrica.falhas_graves]
    texto = {"type": "string"}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["criterios", "falhas_graves", "resumo", "pontos_fortes", "pontos_de_melhoria"],
        "properties": {
            "criterios": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "resultado", "evidencia"],
                    "properties": {
                        "id": {"type": "string", "enum": ids_criterios},
                        "resultado": {"type": "string", "enum": [r.value for r in Resultado]},
                        "evidencia": texto,
                    },
                },
            },
            "falhas_graves": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "ocorreu", "evidencia"],
                    "properties": {
                        "id": {"type": "string", "enum": ids_falhas},
                        "ocorreu": {"type": "boolean"},
                        "evidencia": texto,
                    },
                },
            },
            "resumo": texto,
            "pontos_fortes": {"type": "array", "items": texto},
            "pontos_de_melhoria": {"type": "array", "items": texto},
        },
    }
