"""Provedores de transcrição e avaliação.

O pipeline só conhece estas duas interfaces. ``demo`` roda sem chave e sem
rede (transcrições de exemplo e avaliação por sinais no texto); ``openai``
usa a API da OpenAI. Outro provedor (Whisper local, Azure, Gemini...) é uma
classe nova com o mesmo método.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..rubrica import Rubrica


class ErroIA(RuntimeError):
    """Falha do provedor (rede, limite de uso, resposta vazia). O pipeline tenta de novo depois."""


class Transcritor(Protocol):
    def transcrever(self, arquivo: Path) -> str: ...


class Avaliador(Protocol):
    modelo: str

    def avaliar(self, rubrica: Rubrica, transcricao: str, contexto: dict[str, Any]) -> str | dict[str, Any]:
        """Devolve o julgamento cru (JSON em texto ou dict) para ser validado."""
        ...


def criar_provedores(provedor: str, **opcoes: Any) -> tuple[Transcritor, Avaliador]:
    if provedor == "demo":
        from .demo import AvaliadorDemo, TranscritorDemo

        return TranscritorDemo(), AvaliadorDemo()
    if provedor == "openai":
        from .openai_ia import AvaliadorOpenAI, TranscritorOpenAI, cliente_openai

        cliente = cliente_openai(opcoes["api_key"])
        return (
            TranscritorOpenAI(cliente, opcoes["modelo_transcricao"]),
            AvaliadorOpenAI(cliente, opcoes["modelo_avaliacao"]),
        )
    raise ValueError(f"Provedor de IA desconhecido: {provedor!r} (use 'demo' ou 'openai')")
