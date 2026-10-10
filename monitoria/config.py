"""Configuração lida das variáveis de ambiente (veja .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    banco_url: str
    pasta_arquivos: Path
    rubrica: Path
    ia_provedor: str
    openai_api_key: str
    modelo_transcricao: str
    modelo_avaliacao: str
    usuario: str
    senha: str
    tamanho_maximo_mb: int
    intervalo_worker: float
    max_tentativas: int

    @classmethod
    def do_ambiente(cls, ambiente: dict[str, str] | None = None) -> "Config":
        env = dict(os.environ if ambiente is None else ambiente)
        dados = Path(env.get("PASTA_DADOS", RAIZ / "dados"))
        return cls(
            banco_url=env.get("DATABASE_URL", f"sqlite:///{dados / 'monitoria.db'}"),
            pasta_arquivos=Path(env.get("PASTA_ARQUIVOS", dados / "ligacoes")),
            rubrica=Path(env.get("RUBRICA", RAIZ / "rubricas" / "cobranca.json")),
            ia_provedor=env.get("IA_PROVEDOR", "demo"),
            openai_api_key=env.get("OPENAI_API_KEY", ""),
            modelo_transcricao=env.get("OPENAI_MODELO_TRANSCRICAO", "whisper-1"),
            modelo_avaliacao=env.get("OPENAI_MODELO_AVALIACAO", "gpt-4o-mini"),
            usuario=env.get("MONITORIA_USUARIO", "monitor"),
            senha=env.get("MONITORIA_SENHA", ""),
            tamanho_maximo_mb=int(env.get("TAMANHO_MAXIMO_MB", "100")),
            intervalo_worker=float(env.get("INTERVALO_WORKER", "5")),
            max_tentativas=int(env.get("MAX_TENTATIVAS", "3")),
        )
