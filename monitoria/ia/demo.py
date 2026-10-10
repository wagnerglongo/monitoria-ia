"""Provedor de demonstração: roda sem chave de API e sem rede.

- Transcrição: arquivos ``.txt`` já são a transcrição; para um áudio, usa um
  ``.txt`` com o mesmo nome ao lado dele, se existir.
- Avaliação: procura na fala do operador os ``sinais_demo`` de cada critério
  da rubrica (expressões regulares). É uma heurística, só para a demonstração
  e os testes andarem sem IA; a evidência apontada é a frase que casou.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..rubrica import Rubrica
from . import ErroIA

LINHA_OPERADOR = re.compile(r"^\s*(operador|operadora|atendente)\s*:\s*(.+)$", re.IGNORECASE)


class TranscritorDemo:
    def transcrever(self, arquivo: Path) -> str:
        texto = arquivo if arquivo.suffix.lower() == ".txt" else arquivo.with_suffix(".txt")
        if not texto.exists():
            raise ErroIA(
                "O provedor de demonstração não transcreve áudio. Envie a transcrição em .txt "
                "ou configure IA_PROVEDOR=openai."
            )
        conteudo = texto.read_text(encoding="utf-8").strip()
        if not conteudo:
            raise ErroIA("Transcrição vazia.")
        return conteudo


class AvaliadorDemo:
    modelo = "demo-sinais"

    def avaliar(self, rubrica: Rubrica, transcricao: str, contexto: dict[str, Any]) -> dict[str, Any]:
        falas = [m.group(2).strip() for linha in transcricao.splitlines() if (m := LINHA_OPERADOR.match(linha))]
        if not falas:
            falas = [linha.strip() for linha in transcricao.splitlines() if linha.strip()]
        sem_acordo = contexto.get("tipo") == "sem_acordo"

        criterios = []
        for disciplina in rubrica.disciplinas:
            for criterio in disciplina.criterios:
                evidencia = _procurar(falas, criterio.sinais_demo)
                if evidencia:
                    resultado = "cumpriu"
                elif sem_acordo and criterio.id in {"recapitulacao", "envio_boleto"}:
                    resultado = "nao_se_aplica"
                else:
                    resultado = "nao_cumpriu"
                criterios.append({"id": criterio.id, "resultado": resultado, "evidencia": evidencia or ""})

        falhas = []
        for falha in rubrica.falhas_graves:
            evidencia = _procurar(falas, falha.sinais_demo)
            falhas.append({"id": falha.id, "ocorreu": bool(evidencia), "evidencia": evidencia or ""})

        cumpridos = [c for c in criterios if c["resultado"] == "cumpriu"]
        faltaram = [c["id"] for c in criterios if c["resultado"] == "nao_cumpriu"]
        return {
            "criterios": criterios,
            "falhas_graves": falhas,
            "resumo": f"Avaliação de demonstração: {len(cumpridos)} de {len(criterios)} critérios identificados na fala do operador.",
            "pontos_fortes": [rubrica.criterios[c["id"]][1].descricao for c in cumpridos[:3]],
            "pontos_de_melhoria": [rubrica.criterios[i][1].descricao for i in faltaram[:3]],
        }


FIM_DE_FRASE = re.compile(r"(?<=[.!?])\s+")


def _procurar(falas: list[str], sinais: tuple[str, ...]) -> str | None:
    """Devolve a frase (não a fala inteira) em que o sinal aparece, como faria um monitor."""
    for padrao in sinais:
        regex = re.compile(padrao, re.IGNORECASE)
        for fala in falas:
            for frase in FIM_DE_FRASE.split(fala):
                if regex.search(frase):
                    return frase.strip()[:300]
    return None
