"""Transcrição e avaliação pela API da OpenAI.

A avaliação usa saída estruturada (JSON Schema em modo strict gerado a partir
da rubrica): o modelo só pode responder com os ids de critério da rubrica e
com os três resultados possíveis. O prompt pede classificação com evidência,
nunca nota: quem calcula a nota é ``rubrica.pontuar``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..julgamento import esquema_json
from ..rubrica import Rubrica
from . import ErroIA

INSTRUCOES = """Você é monitor de qualidade de uma operação de cobrança no Brasil.
Avalie a ligação transcrita abaixo usando SOMENTE a rubrica fornecida.

Para cada critério, responda:
- "cumpriu": o operador fez o que o critério pede; cite em "evidencia" o trecho exato da transcrição.
- "nao_cumpriu": o critério se aplicava e não foi feito; explique em uma frase o que faltou.
- "nao_se_aplica": a situação da ligação não pedia aquilo (ex.: não houve acordo, então não há boleto a enviar).

Para cada falha grave, marque "ocorreu": true apenas com evidência clara na transcrição, citando o trecho.
Não calcule nota. Não invente falas que não estão na transcrição. Escreva em português do Brasil.
"""


def cliente_openai(api_key: str) -> Any:
    if not api_key:
        raise ValueError("Defina OPENAI_API_KEY para usar IA_PROVEDOR=openai.")
    from openai import OpenAI

    return OpenAI(api_key=api_key, max_retries=2, timeout=120)


class TranscritorOpenAI:
    def __init__(self, cliente: Any, modelo: str) -> None:
        self.cliente = cliente
        self.modelo = modelo

    def transcrever(self, arquivo: Path) -> str:
        if arquivo.suffix.lower() == ".txt":
            return arquivo.read_text(encoding="utf-8").strip()
        try:
            with arquivo.open("rb") as audio:
                resposta = self.cliente.audio.transcriptions.create(model=self.modelo, file=audio, language="pt")
        except Exception as erro:  # o SDK tem várias exceções; todas viram nova tentativa
            raise ErroIA(f"Falha na transcrição: {erro.__class__.__name__}: {erro}") from erro
        texto = (getattr(resposta, "text", "") or "").strip()
        if not texto:
            raise ErroIA("A transcrição voltou vazia.")
        return texto


class AvaliadorOpenAI:
    def __init__(self, cliente: Any, modelo: str) -> None:
        self.cliente = cliente
        self.modelo = modelo

    def avaliar(self, rubrica: Rubrica, transcricao: str, contexto: dict[str, Any]) -> str:
        try:
            resposta = self.cliente.chat.completions.create(
                model=self.modelo,
                temperature=0,
                messages=[
                    {"role": "system", "content": INSTRUCOES},
                    {"role": "user", "content": montar_pedido(rubrica, transcricao, contexto)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "avaliacao_monitoria", "strict": True, "schema": esquema_json(rubrica)},
                },
            )
        except Exception as erro:
            raise ErroIA(f"Falha na avaliação: {erro.__class__.__name__}: {erro}") from erro

        mensagem = resposta.choices[0].message
        if getattr(mensagem, "refusal", None):
            raise ErroIA(f"O modelo recusou a avaliação: {mensagem.refusal}")
        if not mensagem.content:
            raise ErroIA("O modelo não devolveu conteúdo.")
        return mensagem.content


def montar_pedido(rubrica: Rubrica, transcricao: str, contexto: dict[str, Any]) -> str:
    rubrica_texto = {
        "disciplinas": [
            {"disciplina": d.nome, "criterios": [{"id": c.id, "descricao": c.descricao} for c in d.criterios]}
            for d in rubrica.disciplinas
        ],
        "falhas_graves": [{"id": f.id, "descricao": f.descricao} for f in rubrica.falhas_graves],
    }
    ligacao = {
        "tipo": {"acordo": "ligação com acordo fechado", "sem_acordo": "ligação sem acordo"}.get(contexto.get("tipo", ""), "não informado"),
        "observacoes_do_monitor": contexto.get("observacoes") or "",
    }
    return (
        f"RUBRICA:\n{json.dumps(rubrica_texto, ensure_ascii=False, indent=1)}\n\n"
        f"CONTEXTO:\n{json.dumps(ligacao, ensure_ascii=False)}\n\n"
        f"TRANSCRIÇÃO:\n{transcricao}"
    )
