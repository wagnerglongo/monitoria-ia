"""Recebimento dos arquivos de ligação (áudio ou transcrição pronta).

O arquivo é conferido pelo conteúdo (assinatura do formato), não só pela
extensão, gravado com nome aleatório fora de qualquer pasta pública e com
limite de tamanho aplicado durante a cópia.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import BinaryIO

EXTENSOES = {".mp3", ".wav", ".m4a", ".ogg", ".txt"}


class ArquivoInvalido(ValueError):
    pass


def formato_confere(extensao: str, inicio: bytes) -> bool:
    if extensao == ".mp3":
        return inicio[:3] == b"ID3" or (len(inicio) > 1 and inicio[0] == 0xFF and inicio[1] & 0xE0 == 0xE0)
    if extensao == ".wav":
        return inicio[:4] == b"RIFF" and inicio[8:12] == b"WAVE"
    if extensao == ".ogg":
        return inicio[:4] == b"OggS"
    if extensao == ".m4a":
        return inicio[4:8] == b"ftyp"
    if extensao == ".txt":
        if b"\x00" in inicio:
            return False
        try:
            inicio.decode("utf-8")
        except UnicodeDecodeError as erro:
            # o corte pode cair no meio de um caractere de vários bytes
            return erro.start >= len(inicio) - 3
        return True
    return False


def salvar(origem: BinaryIO, nome_original: str, pasta: Path, tamanho_maximo: int) -> str:
    extensao = Path(nome_original).suffix.lower()
    if extensao not in EXTENSOES:
        raise ArquivoInvalido(f"Formato não aceito. Envie {', '.join(sorted(EXTENSOES))}.")

    inicio = origem.read(64)
    if not formato_confere(extensao, inicio):
        raise ArquivoInvalido("O conteúdo do arquivo não corresponde à extensão informada.")

    pasta.mkdir(parents=True, exist_ok=True)
    nome = f"{uuid.uuid4().hex}{extensao}"
    destino = pasta / nome
    total = len(inicio)
    try:
        with destino.open("wb") as saida:
            saida.write(inicio)
            while bloco := origem.read(1024 * 1024):
                total += len(bloco)
                if total > tamanho_maximo:
                    raise ArquivoInvalido(f"Arquivo maior que o limite de {tamanho_maximo // (1024 * 1024)} MB.")
                saida.write(bloco)
    except ArquivoInvalido:
        destino.unlink(missing_ok=True)
        raise
    if total == 0:
        destino.unlink(missing_ok=True)
        raise ArquivoInvalido("Arquivo vazio.")
    return nome
