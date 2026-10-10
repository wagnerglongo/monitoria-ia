"""Relatório em PDF de uma ligação avaliada, para entregar ao operador no feedback."""

from __future__ import annotations

from fpdf import FPDF

from .modelos import Ligacao
from .rubrica import Rubrica

RESULTADOS = {"cumpriu": "Cumpriu", "nao_cumpriu": "Não cumpriu", "nao_se_aplica": "Não se aplica"}
TINTA = (31, 36, 48)
SUAVE = (96, 104, 118)
LINHA = (220, 222, 228)


def _txt(texto: str | None) -> str:
    """As fontes padrão do PDF são Latin-1: troca o que não cabe (aspas curvas, travessões, emojis)."""
    texto = (texto or "").replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    texto = texto.replace("–", "-").replace("—", "-").replace("…", "...")
    return texto.encode("latin-1", "replace").decode("latin-1")


def gerar_pdf(ligacao: Ligacao, rubrica: Rubrica) -> bytes:
    pdf = FPDF(format="A4")
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(True, margin=16)
    pdf.add_page()
    largura = pdf.w - pdf.l_margin - pdf.r_margin

    pdf.set_text_color(*SUAVE)
    pdf.set_font("Helvetica", size=9)
    pdf.cell(0, 5, _txt(f"{rubrica.nome} - rubrica {ligacao.rubrica_versao or ''}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*TINTA)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, _txt(f"Avaliação da ligação #{ligacao.id}"), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", size=10)
    dados = [
        ("Operador", ligacao.operador),
        ("Contrato", ligacao.contrato),
        ("Data da ligação", ligacao.data_ligacao.strftime("%d/%m/%Y")),
        ("Tipo", "Com acordo" if ligacao.tipo == "acordo" else "Sem acordo"),
        ("Monitor", ligacao.monitor or "-"),
    ]
    for rotulo, valor in dados:
        pdf.set_text_color(*SUAVE)
        pdf.cell(38, 6, _txt(rotulo))
        pdf.set_text_color(*TINTA)
        pdf.cell(0, 6, _txt(valor), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 30)
    nota = ligacao.nota if ligacao.nota is not None else 0
    pdf.set_text_color(*((180, 35, 24) if ligacao.falha_grave or nota < 60 else (12, 110, 70) if nota >= 85 else (150, 95, 10)))
    pdf.cell(40, 14, _txt(f"{nota:.1f}".replace(".", ",")))
    pdf.set_font("Helvetica", size=10)
    pdf.set_text_color(*TINTA)
    if ligacao.falha_grave:
        pdf.multi_cell(0, 5, _txt(f"Nota zerada por falha grave. Sem a falha, a ligação teria {ligacao.nota_sem_falha:.1f} pontos.".replace(".", ",", 1)))
    else:
        pdf.multi_cell(0, 5, _txt("Nota de 0 a 100 calculada a partir da rubrica."))
    pdf.ln(2)

    if ligacao.resumo:
        pdf.set_font("Helvetica", "I", 10)
        pdf.multi_cell(0, 5, _txt(ligacao.resumo))
        pdf.ln(2)

    criterios = {c.criterio: c for c in ligacao.criterios}
    for disciplina in ligacao.disciplinas:
        pdf.ln(2)
        pdf.set_draw_color(*LINHA)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + largura, pdf.get_y())
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(*TINTA)
        pontos = "não se aplica" if disciplina.pontos is None else f"{disciplina.pontos:.1f} de {disciplina.maximo:.0f}".replace(".", ",")
        pdf.cell(largura - 40, 7, _txt(disciplina.nome))
        pdf.cell(40, 7, _txt(pontos), align="R", new_x="LMARGIN", new_y="NEXT")

        rubrica_disciplina = next((d for d in rubrica.disciplinas if d.id == disciplina.disciplina), None)
        for criterio in rubrica_disciplina.criterios if rubrica_disciplina else ():
            nota_criterio = criterios.get(criterio.id)
            if nota_criterio is None:
                continue
            pdf.set_font("Helvetica", "B", 9)
            cor = {"cumpriu": (12, 110, 70), "nao_cumpriu": (180, 35, 24)}.get(nota_criterio.resultado, SUAVE)
            pdf.set_text_color(*cor)
            pdf.cell(28, 5, _txt(RESULTADOS.get(nota_criterio.resultado, nota_criterio.resultado)))
            pdf.set_text_color(*TINTA)
            pdf.set_font("Helvetica", size=9)
            pdf.multi_cell(0, 5, _txt(criterio.descricao), new_x="LMARGIN", new_y="NEXT")
            if nota_criterio.evidencia:
                pdf.set_x(pdf.l_margin + 28)
                pdf.set_text_color(*SUAVE)
                pdf.set_font("Helvetica", "I", 8.5)
                pdf.multi_cell(0, 4.5, _txt(f'"{nota_criterio.evidencia}"'), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)

    julgamento = ligacao.julgamento or {}
    falhas = [f for f in julgamento.get("falhas_graves", []) if f.get("ocorreu")]
    descricoes = {f.id: f.descricao for f in rubrica.falhas_graves}
    if falhas:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(180, 35, 24)
        pdf.cell(0, 7, _txt("Falhas graves"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=9)
        pdf.set_text_color(*TINTA)
        for falha in falhas:
            pdf.multi_cell(0, 5, _txt(f"- {descricoes.get(falha['id'], falha['id'])} \"{falha.get('evidencia', '')}\""), new_x="LMARGIN", new_y="NEXT")

    for titulo, chave in (("Pontos fortes", "pontos_fortes"), ("Pontos de melhoria", "pontos_de_melhoria")):
        itens = julgamento.get(chave) or []
        if itens:
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*TINTA)
            pdf.cell(0, 6, _txt(titulo), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", size=9)
            for item in itens:
                pdf.multi_cell(0, 5, _txt(f"- {item}"), new_x="LMARGIN", new_y="NEXT")

    pdf.set_y(-14)
    pdf.set_font("Helvetica", size=8)
    pdf.set_text_color(*SUAVE)
    pdf.cell(0, 5, _txt(f"Avaliado por {ligacao.modelo or '-'} em {ligacao.processado_em:%d/%m/%Y %H:%M} UTC" if ligacao.processado_em else ""))
    return bytes(pdf.output())
