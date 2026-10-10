"""Tabelas (SQLAlchemy 2). Funciona com SQLite (padrão) e PostgreSQL (docker-compose)."""

from __future__ import annotations

import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def agora() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Status(str, enum.Enum):
    PENDENTE = "pendente"
    PROCESSANDO = "processando"
    CONCLUIDA = "concluida"
    ERRO = "erro"


class Ligacao(Base):
    __tablename__ = "ligacoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    arquivo: Mapped[str] = mapped_column(String(255), unique=True)
    nome_original: Mapped[str] = mapped_column(String(255))
    operador: Mapped[str] = mapped_column(String(120), index=True)
    contrato: Mapped[str] = mapped_column(String(40))
    tipo: Mapped[str] = mapped_column(String(20))  # acordo | sem_acordo
    monitor: Mapped[str | None] = mapped_column(String(120))
    data_ligacao: Mapped[date] = mapped_column(Date)
    observacoes: Mapped[str | None] = mapped_column(Text)

    status: Mapped[Status] = mapped_column(Enum(Status, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e]), default=Status.PENDENTE, index=True)
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    proxima_tentativa: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    iniciado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    erro: Mapped[str | None] = mapped_column(Text)

    transcricao: Mapped[str | None] = mapped_column(Text)
    nota: Mapped[float | None] = mapped_column(Float)
    nota_sem_falha: Mapped[float | None] = mapped_column(Float)
    falha_grave: Mapped[bool] = mapped_column(Boolean, default=False)
    resumo: Mapped[str | None] = mapped_column(Text)
    julgamento: Mapped[dict | None] = mapped_column(JSON)
    rubrica_versao: Mapped[str | None] = mapped_column(String(40))
    modelo: Mapped[str | None] = mapped_column(String(80))

    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    processado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    disciplinas: Mapped[list["NotaDisciplina"]] = relationship(back_populates="ligacao", cascade="all, delete-orphan", order_by="NotaDisciplina.id")
    criterios: Mapped[list["NotaCriterio"]] = relationship(back_populates="ligacao", cascade="all, delete-orphan", order_by="NotaCriterio.id")


class NotaDisciplina(Base):
    __tablename__ = "notas_disciplina"

    id: Mapped[int] = mapped_column(primary_key=True)
    ligacao_id: Mapped[int] = mapped_column(ForeignKey("ligacoes.id", ondelete="CASCADE"), index=True)
    disciplina: Mapped[str] = mapped_column(String(60))
    nome: Mapped[str] = mapped_column(String(120))
    pontos: Mapped[float | None] = mapped_column(Float)
    maximo: Mapped[float] = mapped_column(Float)

    ligacao: Mapped[Ligacao] = relationship(back_populates="disciplinas")


class NotaCriterio(Base):
    __tablename__ = "notas_criterio"

    id: Mapped[int] = mapped_column(primary_key=True)
    ligacao_id: Mapped[int] = mapped_column(ForeignKey("ligacoes.id", ondelete="CASCADE"), index=True)
    disciplina: Mapped[str] = mapped_column(String(60))
    criterio: Mapped[str] = mapped_column(String(60))
    peso: Mapped[float] = mapped_column(Float)
    resultado: Mapped[str] = mapped_column(String(20))
    pontos: Mapped[float] = mapped_column(Float)
    evidencia: Mapped[str] = mapped_column(Text, default="")

    ligacao: Mapped[Ligacao] = relationship(back_populates="criterios")


def criar_motor(url: str) -> Engine:
    if url.startswith("sqlite:///"):
        from pathlib import Path

        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    motor = create_engine(url, future=True, pool_pre_ping=True)
    if motor.dialect.name == "sqlite":

        @event.listens_for(motor, "connect")
        def _pragmas(conexao, _):  # type: ignore[no-untyped-def]
            cursor = conexao.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA busy_timeout = 5000")
            cursor.close()

    Base.metadata.create_all(motor)
    return motor


def fabrica_sessoes(motor: Engine) -> sessionmaker:
    return sessionmaker(motor, expire_on_commit=False)
