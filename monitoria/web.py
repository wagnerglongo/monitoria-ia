"""Painel web e API (FastAPI).

Telas: painel com indicadores e ranking, envio de ligação, detalhe com a
transcrição e as evidências marcadas, PDF para feedback. A API JSON expõe
o mesmo conteúdo para integrar com discador, BI ou n8n.
"""


import hmac
import re
import secrets
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy.orm import sessionmaker

from . import consultas
from .armazenamento import ArquivoInvalido, salvar
from .config import Config
from .ia import criar_provedores
from .modelos import Ligacao, Status, criar_motor, fabrica_sessoes
from .pipeline import Pipeline
from .relatorio import RESULTADOS, gerar_pdf
from .rubrica import Rubrica

PASTA = Path(__file__).parent
basic = HTTPBasic(auto_error=False)


def criar_app(config: Config, sessoes: sessionmaker | None = None) -> FastAPI:
    if not config.senha and config.ia_provedor != "demo":
        raise RuntimeError("Defina MONITORIA_SENHA: o painel mostra gravações e transcrições de clientes.")

    rubrica = Rubrica.carregar(config.rubrica)
    sessoes = sessoes or fabrica_sessoes(criar_motor(config.banco_url))
    app = FastAPI(title="Monitoria IA", version="1.0.0", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.mount("/static", StaticFiles(directory=PASTA / "static"), name="static")
    paginas = Jinja2Templates(directory=PASTA / "templates")
    paginas.env.filters["nota"] = lambda v: "-" if v is None else f"{v:.1f}".replace(".", ",")
    paginas.env.globals["resultados"] = RESULTADOS

    def autenticar(credenciais: Annotated[HTTPBasicCredentials | None, Depends(basic)]) -> str:
        if not config.senha:
            return "demo"
        ok = credenciais is not None and hmac.compare_digest(credenciais.username.encode(), config.usuario.encode()) and hmac.compare_digest(
            credenciais.password.encode(), config.senha.encode()
        )
        if not ok:
            raise HTTPException(401, "Autenticação necessária.", headers={"WWW-Authenticate": 'Basic realm="Monitoria"'})
        return credenciais.username

    Usuario = Annotated[str, Depends(autenticar)]

    def pipeline() -> Pipeline:
        transcritor, avaliador = criar_provedores(
            config.ia_provedor,
            api_key=config.openai_api_key,
            modelo_transcricao=config.modelo_transcricao,
            modelo_avaliacao=config.modelo_avaliacao,
        )
        return Pipeline(sessoes, rubrica, transcritor, avaliador, config.pasta_arquivos, config.max_tentativas)

    def pagina(request: Request, template: str, contexto: dict[str, Any], status: int = 200) -> HTMLResponse:
        token = request.cookies.get("csrf") or secrets.token_urlsafe(24)
        resposta = paginas.TemplateResponse(
            request, template, {"csrf": token, "demo": config.ia_provedor == "demo", "rubrica": rubrica, **contexto}, status_code=status
        )
        resposta.set_cookie("csrf", token, httponly=True, samesite="strict")
        return resposta

    def conferir_csrf(request: Request, token: str) -> None:
        if not token or not hmac.compare_digest(token, request.cookies.get("csrf", "")):
            raise HTTPException(403, "Formulário expirado. Recarregue a página.")

    @app.middleware("http")
    async def cabecalhos(request: Request, call_next):  # type: ignore[no-untyped-def]
        resposta = await call_next(request)
        resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
        resposta.headers.setdefault("X-Frame-Options", "DENY")
        resposta.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.startswith("/api/docs"):
            resposta.headers.setdefault(
                "Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
            )
        return resposta

    # ---- telas -----------------------------------------------------------

    @app.get("/saude", include_in_schema=False)
    def saude() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def painel(request: Request, _: Usuario, operador: str | None = None) -> HTMLResponse:
        with sessoes() as sessao:
            return pagina(
                request,
                "painel.html",
                {
                    "indicadores": consultas.indicadores(sessao),
                    "ranking": consultas.ranking_operadores(sessao),
                    "disciplinas": consultas.media_por_disciplina(sessao),
                    "ligacoes": consultas.ultimas(sessao, operador=operador),
                    "filtro_operador": operador,
                },
            )

    @app.get("/ligacoes/nova", response_class=HTMLResponse)
    def nova(request: Request, _: Usuario) -> HTMLResponse:
        return pagina(request, "nova.html", {"hoje": date.today().isoformat(), "erro": None, "valores": {}})

    @app.post("/ligacoes")
    def enviar(
        request: Request,
        usuario: Usuario,
        arquivo: Annotated[UploadFile, File()],
        operador: Annotated[str, Form(max_length=120)],
        contrato: Annotated[str, Form(max_length=40)],
        tipo: Annotated[str, Form(pattern="^(acordo|sem_acordo)$")],
        data_ligacao: Annotated[date, Form()],
        csrf: Annotated[str, Form()] = "",
        monitor: Annotated[str, Form(max_length=120)] = "",
        observacoes: Annotated[str, Form(max_length=2000)] = "",
    ) -> Response:
        conferir_csrf(request, csrf)
        try:
            ligacao = _registrar(arquivo, operador, contrato, tipo, data_ligacao, monitor or usuario, observacoes)
        except ArquivoInvalido as erro:
            valores = {"operador": operador, "contrato": contrato, "tipo": tipo, "data_ligacao": data_ligacao.isoformat(), "observacoes": observacoes}
            return pagina(request, "nova.html", {"hoje": date.today().isoformat(), "erro": str(erro), "valores": valores}, 422)
        return RedirectResponse(f"/ligacoes/{ligacao.id}", status_code=303)

    @app.get("/ligacoes/{ligacao_id}", response_class=HTMLResponse)
    def detalhe(request: Request, ligacao_id: int, _: Usuario) -> HTMLResponse:
        with sessoes() as sessao:
            ligacao = sessao.get(Ligacao, ligacao_id)
            if ligacao is None:
                raise HTTPException(404, "Ligação não encontrada.")
            ligacao.disciplinas, ligacao.criterios  # carrega antes de fechar a sessão
            return pagina(request, "ligacao.html", {"ligacao": ligacao, "transcricao": marcar_evidencias(ligacao)})

    @app.post("/ligacoes/{ligacao_id}/reprocessar")
    def reprocessar(request: Request, ligacao_id: int, _: Usuario, csrf: Annotated[str, Form()] = "") -> Response:
        conferir_csrf(request, csrf)
        pipeline().reprocessar(ligacao_id)
        return RedirectResponse(f"/ligacoes/{ligacao_id}", status_code=303)

    @app.get("/ligacoes/{ligacao_id}/relatorio.pdf")
    def relatorio(ligacao_id: int, _: Usuario) -> Response:
        with sessoes() as sessao:
            ligacao = sessao.get(Ligacao, ligacao_id)
            if ligacao is None or ligacao.status != Status.CONCLUIDA:
                raise HTTPException(404, "Relatório disponível só para ligações avaliadas.")
            pdf = gerar_pdf(ligacao, rubrica)
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="monitoria-{ligacao_id}.pdf"'})

    # ---- API JSON ----------------------------------------------------------

    @app.get("/api/ligacoes")
    def api_listar(_: Usuario, operador: str | None = None, limite: int = 50) -> list[dict[str, Any]]:
        with sessoes() as sessao:
            return [_resumo(l) for l in consultas.ultimas(sessao, min(max(limite, 1), 200), operador)]

    @app.get("/api/ligacoes/{ligacao_id}")
    def api_detalhe(ligacao_id: int, _: Usuario) -> dict[str, Any]:
        with sessoes() as sessao:
            ligacao = sessao.get(Ligacao, ligacao_id)
            if ligacao is None:
                raise HTTPException(404, "Ligação não encontrada.")
            return {
                **_resumo(ligacao),
                "transcricao": ligacao.transcricao,
                "resumo": ligacao.resumo,
                "disciplinas": [{"id": d.disciplina, "nome": d.nome, "pontos": d.pontos, "maximo": d.maximo} for d in ligacao.disciplinas],
                "criterios": [
                    {"id": c.criterio, "disciplina": c.disciplina, "resultado": c.resultado, "pontos": c.pontos, "evidencia": c.evidencia}
                    for c in ligacao.criterios
                ],
                "julgamento": ligacao.julgamento,
            }

    @app.post("/api/ligacoes", status_code=201)
    def api_enviar(
        request: Request,
        usuario: Usuario,
        arquivo: Annotated[UploadFile, File()],
        operador: Annotated[str, Form(max_length=120)],
        contrato: Annotated[str, Form(max_length=40)],
        tipo: Annotated[str, Form(pattern="^(acordo|sem_acordo)$")],
        data_ligacao: Annotated[date, Form()],
        monitor: Annotated[str, Form(max_length=120)] = "",
        observacoes: Annotated[str, Form(max_length=2000)] = "",
    ) -> dict[str, Any]:
        # Navegador não manda cabeçalho próprio em requisição cruzada sem CORS: isso
        # basta para impedir que outro site use a sessão Basic do supervisor (CSRF).
        if request.headers.get("x-requested-with", "").lower() != "monitoria":
            raise HTTPException(400, "Envie o cabeçalho X-Requested-With: monitoria.")
        try:
            ligacao = _registrar(arquivo, operador, contrato, tipo, data_ligacao, monitor or usuario, observacoes)
        except ArquivoInvalido as erro:
            raise HTTPException(422, str(erro)) from erro
        return _resumo(ligacao)

    @app.get("/api/operadores")
    def api_operadores(_: Usuario) -> list[dict[str, Any]]:
        with sessoes() as sessao:
            return [vars(linha) for linha in consultas.ranking_operadores(sessao)]

    # ---- apoio -------------------------------------------------------------

    def _registrar(arquivo: UploadFile, operador: str, contrato: str, tipo: str, data_ligacao: date, monitor: str, observacoes: str) -> Ligacao:
        operador, contrato = operador.strip(), contrato.strip()
        if not operador or not contrato:
            raise ArquivoInvalido("Informe o operador e o contrato.")
        nome = salvar(arquivo.file, arquivo.filename or "", config.pasta_arquivos, config.tamanho_maximo_mb * 1024 * 1024)
        with sessoes() as sessao:
            ligacao = Ligacao(
                arquivo=nome,
                nome_original=(arquivo.filename or nome)[:255],
                operador=operador,
                contrato=contrato,
                tipo=tipo,
                monitor=monitor or None,
                data_ligacao=data_ligacao,
                observacoes=observacoes.strip() or None,
            )
            sessao.add(ligacao)
            sessao.commit()
            return ligacao

    app.state.sessoes = sessoes
    app.state.rubrica = rubrica
    app.state.pipeline = pipeline
    return app


def _resumo(ligacao: Ligacao) -> dict[str, Any]:
    return {
        "id": ligacao.id,
        "operador": ligacao.operador,
        "contrato": ligacao.contrato,
        "tipo": ligacao.tipo,
        "data_ligacao": ligacao.data_ligacao.isoformat(),
        "status": ligacao.status.value,
        "nota": ligacao.nota,
        "falha_grave": ligacao.falha_grave,
        "erro": ligacao.erro,
    }


FALANTE = re.compile(r"^\s*([A-Za-zÀ-ÿ ]{2,20})\s*:\s*(.*)$")


def marcar_evidencias(ligacao: Ligacao) -> list[dict[str, Any]]:
    """Quebra a transcrição em falas e marca as que foram citadas como evidência."""
    if not ligacao.transcricao:
        return []
    evidencias: list[tuple[str, str]] = [(c.criterio, c.evidencia.strip()) for c in ligacao.criterios if c.evidencia and c.resultado == "cumpriu"]
    for falha in (ligacao.julgamento or {}).get("falhas_graves", []):
        if falha.get("ocorreu") and falha.get("evidencia"):
            evidencias.append(("falha:" + falha["id"], falha["evidencia"].strip()))

    falas = []
    for linha in ligacao.transcricao.splitlines():
        if not linha.strip():
            continue
        m = FALANTE.match(linha)
        falante, texto = (m.group(1).strip(), m.group(2)) if m else ("", linha.strip())
        marcas = [cid for cid, ev in evidencias if ev and (ev[:120] in texto or texto[:120] in ev)]
        falas.append(
            {
                "falante": falante,
                "operador": falante.lower() in {"operador", "operadora", "atendente"},
                "texto": Markup(escape(texto)),
                "marcas": marcas,
                "falha": any(m.startswith("falha:") for m in marcas),
            }
        )
    return falas
