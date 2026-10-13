# Relatórios em .docx — pedido do usuário ("os relatórios que devemos
# gerar é interessante que sejam em docx"). Gerados aqui no backend (não no
# navegador) porque é aqui que mora o dado de verdade, cross-dispositivo —
# um relatório gerado no frontend só veria o que aquele aparelho sincronizou.
# `python-docx` monta o .docx inteiro em memória (BytesIO), devolvido como
# download — nada fica salvo em disco no servidor.

from datetime import datetime, timezone
from io import BytesIO

from flask import Blueprint, g, request, send_file
from docx import Document
from docx.shared import Pt

from app.models import Avaria
from app.auth.utils import login_obrigatorio
from app.metricas.routes import calcular_perdas_por_mercado

bp = Blueprint("relatorios", __name__, url_prefix="/api/relatorios")


def _cabecalho(doc: Document, titulo: str, subtitulo: str) -> None:
    doc.add_heading(titulo, level=1)
    p = doc.add_paragraph(subtitulo)
    p.runs[0].italic = True
    agora = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    rodape = doc.add_paragraph(f"Gerado em {agora}")
    rodape.runs[0].font.size = Pt(9)
    doc.add_paragraph()


def _tabela(doc: Document, colunas: list[str]) -> "object":
    tabela = doc.add_table(rows=1, cols=len(colunas))
    tabela.style = "Light Grid Accent 1"
    cabecalho = tabela.rows[0].cells
    for i, titulo in enumerate(colunas):
        cabecalho[i].text = titulo
        for run in cabecalho[i].paragraphs[0].runs:
            run.bold = True
    return tabela


def _resposta_docx(doc: Document, nome_arquivo: str):
    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        as_attachment=True,
        download_name=nome_arquivo,
    )


@bp.get("/avarias.docx")
@login_obrigatorio()
def relatorio_avarias():
    """Aberto pros dois perfis — mesmo nível de sensibilidade da listagem
    crua (GET /api/avarias): um operador pode querer o próprio relatório
    como comprovante do que registrou."""
    avarias = Avaria.query.filter_by(empresa_id=g.empresa_id).order_by(Avaria.registrado_em.desc()).all()

    doc = Document()
    _cabecalho(doc, "Relatório de Avarias e Quebras", f"{len(avarias)} registro(s) — histórico completo")

    if not avarias:
        doc.add_paragraph("Nenhuma avaria registrada até o momento.")
    else:
        tabela = _tabela(doc, ["Data", "Mercado", "EAN", "Produto", "Qtd.", "Operador", "Motivo"])
        for a in avarias:
            linha = tabela.add_row().cells
            linha[0].text = a.registrado_em.strftime("%d/%m/%Y %H:%M") if a.registrado_em else "—"
            linha[1].text = a.nome_cliente or a.mercado_slug
            linha[2].text = a.codigo_ean or "—"
            linha[3].text = a.descricao or "—"
            linha[4].text = str(a.quantidade)
            linha[5].text = a.operador or "—"
            linha[6].text = a.motivo or "—"

    nome = f"relatorio-avarias-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.docx"
    return _resposta_docx(doc, nome)


@bp.get("/perdas.docx")
@login_obrigatorio(perfis=["admin"])
def relatorio_perdas():
    """Admin-only — mesma regra de acesso de GET /api/metricas/perdas, já
    que é literalmente a mesma agregação (`calcular_perdas_por_mercado`),
    só que formatada como documento em vez de JSON."""
    dias = int(request.args.get("dias", 30))
    dados = calcular_perdas_por_mercado(g.empresa_id, dias)

    doc = Document()
    _cabecalho(
        doc,
        "Relatório Geral de Perdas",
        f"Últimos {dias} dias — {len(dados['mercados'])} mercado(s) com movimentação de saída",
    )

    if not dados["mercados"]:
        doc.add_paragraph("Nenhuma saída (venda/avaria/vencimento) registrada na janela selecionada.")
    else:
        tabela = _tabela(doc, ["Mercado", "Avaria", "Vencimento", "Venda", "Total perdas", "% perdas"])
        for m in dados["mercados"]:
            linha = tabela.add_row().cells
            linha[0].text = m["mercadoNome"]
            linha[1].text = str(m["avaria"])
            linha[2].text = str(m["vencimento"])
            linha[3].text = str(m["venda"])
            linha[4].text = str(m["totalPerdas"])
            linha[5].text = f"{m['percentual']}%"

    nome = f"relatorio-perdas-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.docx"
    return _resposta_docx(doc, nome)
