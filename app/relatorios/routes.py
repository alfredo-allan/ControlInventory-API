# Relatórios em .docx — pedido do usuário ("os relatórios que devemos
# gerar é interessante que sejam em docx"). Gerados aqui no backend (não no
# navegador) porque é aqui que mora o dado de verdade, cross-dispositivo —
# um relatório gerado no frontend só veria o que aquele aparelho sincronizou.
# `python-docx` monta o .docx inteiro em memória (BytesIO), devolvido como
# download — nada fica salvo em disco no servidor.

from datetime import datetime, timezone
from io import BytesIO

from flask import Blueprint, g, jsonify, request, send_file
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from app.models import Avaria, Mercado, Movimentacao, PedidoItem, Produto, SaldoEstoque
from app.auth.utils import login_obrigatorio
from app.estoque_analytics import calcular_sugestao_completa
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


# --------------------------------------------------------------------------
# Identidade visual do relatório de Giro Registrado — copiada de um modelo
# de referência (.docx) que o usuário enviou em 2026-10-14 ("deixar o
# relatório mais ou menos com essa identidade visual e estrutura"). Cores e
# fontes extraídas direto do XML do arquivo de referência com python-docx,
# não "no olho" — pra bater exatamente com o que ele pediu.
# --------------------------------------------------------------------------
_COR_VERMELHO = RGBColor(0xB4, 0x28, 0x28)
_COR_CINZA_META = RGBColor(0x64, 0x64, 0x64)
_COR_TEXTO = RGBColor(0x28, 0x28, 0x28)
_FILL_CABECALHO = "B42828"
_FILL_ZEBRA_CLARA = "FFFFFF"
_FILL_ZEBRA_ESCURA = "F9F9F9"
_FONTE = "Arial"


def _sombrear_celula(cell, hex_cor: str) -> None:
    """Pinta o fundo de uma célula de tabela — python-docx não tem API de
    alto nível pra isso, precisa manipular o XML (`w:shd`) direto."""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_cor)
    tcPr.append(shd)


def _escrever_celula(cell, texto: str, *, negrito: bool = False, tamanho: int = 9, cor: RGBColor = _COR_TEXTO,
                      alinhamento=WD_ALIGN_PARAGRAPH.CENTER, segunda_linha: str | None = None) -> None:
    """Escreve o texto principal de uma célula (fonte/tamanho/cor/negrito
    consistentes com o modelo de referência) e, opcionalmente, uma segunda
    linha menor/itálica abaixo (usada pra encaixar a sugestão calculada sem
    abrir uma 5ª coluna)."""
    cell.text = ""
    paragrafo = cell.paragraphs[0]
    paragrafo.alignment = alinhamento
    run = paragrafo.add_run(texto)
    run.font.name = _FONTE
    run.font.size = Pt(tamanho)
    run.font.bold = negrito
    run.font.color.rgb = cor
    if segunda_linha:
        p2 = cell.add_paragraph()
        p2.alignment = alinhamento
        r2 = p2.add_run(segunda_linha)
        r2.font.name = _FONTE
        r2.font.size = Pt(7.5)
        r2.font.italic = True
        r2.font.color.rgb = _COR_CINZA_META


def _classificar_status(quantidade_giro: int, saldo, resultado_calculo) -> str:
    """Rótulo da coluna 'Status / Observação' — mesma nomenclatura do
    modelo de referência (Normal / Estoque Baixo / Estoque Crítico /
    Estoque Alto / Ruptura). 'Ruptura' é o que o operador marcou de
    propósito no Giro Registrado (quantidade=0), tem prioridade sobre
    qualquer cálculo. Os demais vêm da urgência já calculada por
    `calcular_sugestao_completa` (mesma fonte da sugestão numérica, pra não
    ter duas lógicas divergentes dizendo coisas diferentes sobre o mesmo
    item)."""
    if quantidade_giro == 0:
        return "Ruptura / Atenção"
    if saldo is None or resultado_calculo is None:
        return "Sem dados para cálculo"
    urgencia = resultado_calculo["urgencia"]
    if urgencia == "critico":
        return "Estoque Crítico"
    if urgencia == "atencao":
        return "Estoque Baixo"
    if resultado_calculo["sugestaoPedido"] == 0:
        return "Estoque Alto"
    return "Normal"


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
            linha[4].text = f"{a.quantidade} {'cx' if a.quantity_type == 'caixa' else 'un'}"
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


@bp.get("/giro-registrado.docx")
@login_obrigatorio()
def relatorio_giro_registrado():
    """Giro Registrado ("Qtd. a pedir") de UM mercado + UM ciclo — reflexo
    da aba "Giro Registrado" do Painel de Sugestão. Aberto pros dois
    perfis, mesmo nível de sensibilidade de /api/avarias e /api/pedidos: o
    operador também quer poder baixar o giro do mercado em que está
    trabalhando. Aceita um filtro opcional `operador`, pra quando o admin
    quiser o giro de só um operador dentro do mercado.

    2026-10-14 (PDF "Questionamento Técnico", seção 3): o documento traz,
    por item, a quantidade atual em estoque (`SaldoEstoque`) e a sugestão
    de pedido CALCULADA (`calcular_sugestao_completa`, port em Python do
    mesmo algoritmo da tela do Painel de Sugestão — ver o aviso de
    divergência de configuração em app/estoque_analytics.py).

    2026-10-14, rodada seguinte: o usuário mandou um .docx de referência
    ("Giro_Registrado_Max_Muffato_W42.docx") pedindo pra bater a
    identidade visual com aquele modelo — uma TABELA com cabeçalho
    vermelho, zebra striping e coluna de status, não mais um parágrafo por
    produto. Isso inverte o pedido anterior ("sem tabela, só texto
    corrido") — o modelo de referência concreto é o sinal mais forte de
    intenção, então essa versão segue ele. Cores/fontes extraídas do XML
    do arquivo de referência (ver constantes no topo do módulo)."""
    mercado_slug = (request.args.get("mercadoId") or "").strip()
    ciclo_id = (request.args.get("cicloId") or "").strip()
    operador_filtro = (request.args.get("operador") or "").strip()
    if not mercado_slug or not ciclo_id:
        return jsonify({"erro": "Parâmetros mercadoId e cicloId são obrigatórios."}), 400

    mercado = Mercado.query.filter_by(empresa_id=g.empresa_id, slug=mercado_slug).first()
    nome_mercado = mercado.nome if mercado else mercado_slug

    consulta_itens = PedidoItem.query.filter_by(empresa_id=g.empresa_id, mercado_slug=mercado_slug, ciclo_id=ciclo_id)
    if operador_filtro:
        consulta_itens = consulta_itens.filter_by(operador=operador_filtro)
    itens = consulta_itens.order_by(PedidoItem.atualizado_em.desc()).all()

    # Descrição vem do catálogo (Produto) — o PedidoItem só guarda o EAN.
    eans = [i.codigo_ean for i in itens]
    produtos_por_ean = (
        {p.codigo_ean: p for p in Produto.query.filter_by(empresa_id=g.empresa_id).filter(Produto.codigo_ean.in_(eans))}
        if eans
        else {}
    )

    # Estoque atual (SaldoEstoque é FK por id, não por slug/EAN) + histórico
    # de movimentações do mercado inteiro, buscado uma vez só e agrupado por
    # EAN — evita 1 query por item pra cada um dos dois cálculos.
    produto_ids = [p.id for p in produtos_por_ean.values()]
    saldos_por_produto_id = (
        {s.produto_id: s for s in SaldoEstoque.query.filter_by(mercado_id=mercado.id).filter(SaldoEstoque.produto_id.in_(produto_ids))}
        if mercado and produto_ids
        else {}
    )
    movs_por_ean: dict[str, list] = {}
    if mercado:
        for mv in Movimentacao.query.filter_by(empresa_id=g.empresa_id, mercado_slug=mercado_slug).all():
            movs_por_ean.setdefault(mv.codigo_ean, []).append(mv)

    doc = Document()
    doc.styles["Normal"].font.name = _FONTE
    doc.styles["Normal"].font.size = Pt(10)

    # Título — mesma cor/fonte/tamanho do modelo de referência (não usa
    # `doc.add_heading`, que aplicaria o estilo "Heading 1" padrão do Word
    # em vez do vermelho/Arial que o usuário pediu pra replicar).
    p_titulo = doc.add_paragraph()
    r_titulo = p_titulo.add_run("RELATÓRIO DE GIRO REGISTRADO E SUGESTÃO DE PEDIDO")
    r_titulo.font.name = _FONTE
    r_titulo.font.size = Pt(16)
    r_titulo.font.bold = True
    r_titulo.font.color.rgb = _COR_VERMELHO

    nomes_operadores = {i.operador for i in itens if i.operador}
    if operador_filtro:
        rotulo_operador = operador_filtro
    elif len(nomes_operadores) == 1:
        rotulo_operador = next(iter(nomes_operadores))
    elif nomes_operadores:
        rotulo_operador = "Vários operadores"
    else:
        rotulo_operador = "—"

    hoje = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    p_meta = doc.add_paragraph()
    r_meta = p_meta.add_run(
        f"Cliente: {nome_mercado}  |  Ciclo: {ciclo_id}  |  Total de Itens: {len(itens)}  |  "
        f"Data de Geração: {hoje}  |  Operador: {rotulo_operador}"
    )
    r_meta.font.name = _FONTE
    r_meta.font.size = Pt(9.5)
    r_meta.font.italic = True
    r_meta.font.color.rgb = _COR_CINZA_META

    p_intro = doc.add_paragraph()
    texto_intro = (
        f"Abaixo segue o consolidado dos dados de estoque atual e sugestões de pedido do ciclo "
        f"{ciclo_id}, referente ao cliente {nome_mercado}"
    )
    texto_intro += f", filtrado para o operador {operador_filtro}." if operador_filtro else "."
    r_intro = p_intro.add_run(texto_intro)
    r_intro.font.name = _FONTE
    r_intro.font.size = Pt(10.5)
    r_intro.font.color.rgb = _COR_TEXTO
    doc.add_paragraph()

    rupturas = 0
    criticos = 0
    if not itens:
        p_vazio = doc.add_paragraph()
        p_vazio.add_run("Nenhum item registrado neste ciclo ainda.").font.name = _FONTE
    else:
        # Ordena por descrição — mais fácil de achar um produto específico
        # numa lista impressa do que pela ordem de edição.
        itens_ordenados = sorted(
            itens, key=lambda i: (produtos_por_ean[i.codigo_ean].descricao if i.codigo_ean in produtos_por_ean else i.codigo_ean)
        )

        tabela = doc.add_table(rows=1, cols=4)
        tabela.alignment = WD_TABLE_ALIGNMENT.CENTER
        tabela.autofit = False
        larguras = [Inches(3.1), Inches(1.4), Inches(1.6), Inches(1.9)]
        titulos = ["Descrição do Produto", "Estoque Atual", "Sugestão Pedido", "Status / Observação"]
        for i, (titulo, largura) in enumerate(zip(titulos, larguras)):
            celula = tabela.rows[0].cells[i]
            celula.width = largura
            _sombrear_celula(celula, _FILL_CABECALHO)
            _escrever_celula(
                celula, titulo, negrito=True, tamanho=9.5, cor=RGBColor(0xFF, 0xFF, 0xFF),
                alinhamento=WD_ALIGN_PARAGRAPH.LEFT if i == 0 else WD_ALIGN_PARAGRAPH.CENTER,
            )
        tabela.columns[0].width = larguras[0]
        tabela.columns[1].width = larguras[1]
        tabela.columns[2].width = larguras[2]
        tabela.columns[3].width = larguras[3]

        for indice, item in enumerate(itens_ordenados):
            produto = produtos_por_ean.get(item.codigo_ean)
            descricao = produto.descricao if produto else f"EAN {item.codigo_ean}"
            saldo = saldos_por_produto_id.get(produto.id) if produto else None
            resultado = None
            if saldo is not None and produto is not None:
                resultado = calcular_sugestao_completa(
                    movs_por_ean.get(item.codigo_ean, []),
                    quantidade_atual=saldo.quantidade_atual or 0,
                    validade_media_dias=produto.validade_media_dias,
                )

            ruptura = item.quantidade == 0
            if ruptura:
                rupturas += 1
            if resultado and resultado["urgencia"] == "critico" and not ruptura:
                criticos += 1

            rotulo_unidade = "cx" if item.quantity_type == "caixa" else "un"
            fill = _FILL_ZEBRA_CLARA if indice % 2 == 0 else _FILL_ZEBRA_ESCURA
            linha = tabela.add_row().cells
            for c in linha:
                _sombrear_celula(c, fill)

            _escrever_celula(linha[0], descricao, negrito=ruptura, tamanho=9, alinhamento=WD_ALIGN_PARAGRAPH.LEFT)

            texto_estoque = f"{saldo.quantidade_atual} {rotulo_unidade}" if saldo is not None else "—"
            _escrever_celula(linha[1], texto_estoque, tamanho=9)

            if ruptura:
                _escrever_celula(linha[2], "RUPTURA (Sem estoque)", tamanho=9)
            else:
                texto_sugestao_calc = (
                    f"Sugestão calculada: {resultado['sugestaoPedido']} {rotulo_unidade}" if resultado else None
                )
                _escrever_celula(
                    linha[2], f"{item.quantidade} {rotulo_unidade}", tamanho=9, segunda_linha=texto_sugestao_calc
                )

            status = _classificar_status(item.quantidade, saldo, resultado)
            _escrever_celula(linha[3], status, negrito=ruptura, tamanho=9)

        doc.add_paragraph()

        p_obs_titulo = doc.add_paragraph()
        r_obs_titulo = p_obs_titulo.add_run("Observações Comerciais:")
        r_obs_titulo.font.name = _FONTE
        r_obs_titulo.font.size = Pt(11)
        r_obs_titulo.font.bold = True
        r_obs_titulo.font.color.rgb = _COR_VERMELHO

        observacoes = [
            f"Operador responsável pelo giro deste ciclo: {rotulo_operador}."
            if operador_filtro or len(nomes_operadores) == 1
            else "Este ciclo tem registros de mais de um operador — veja a coluna de origem em Admin › Atividade por operador pra detalhar quem registrou cada item.",
        ]
        if rupturas:
            observacoes.append(
                f"{rupturas} item(ns) em situação de ruptura — marcados sem estoque pelo operador no momento do registro."
            )
        if criticos:
            observacoes.append(
                f"{criticos} item(ns) com estoque crítico — cobertura abaixo do prazo mínimo de reposição; priorize o pedido desses itens."
            )
        observacoes.append(
            "A sugestão calculada usa os parâmetros padrão do sistema (lead time e janela de pesquisa). "
            "Se este mercado usa uma configuração personalizada no aparelho do operador, o número na tela "
            "pode divergir ligeiramente do impresso aqui."
        )
        for texto_obs in observacoes:
            p_obs = doc.add_paragraph(style="List Bullet")
            r_obs = p_obs.add_run(texto_obs)
            r_obs.font.name = _FONTE
            r_obs.font.size = Pt(9.5)
            r_obs.font.color.rgb = _COR_TEXTO

    sufixo_operador = f"-{operador_filtro}" if operador_filtro else ""
    nome = f"giro-registrado-{mercado_slug}-{ciclo_id}{sufixo_operador}.docx"
    return _resposta_docx(doc, nome)
