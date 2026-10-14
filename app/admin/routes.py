# Rotas administrativas sensíveis — tudo aqui é @login_obrigatorio(perfis=
# ["admin"]). Agrupadas num blueprint próprio (em vez de espalhadas em
# catalogo/estoque) porque são operações de outra natureza: auditoria e
# limpeza de dados, não leitura/escrita operacional do dia a dia.

from flask import Blueprint, request, jsonify, g

from app.extensions import db
from app.models import LoginLog, Avaria, Movimentacao, PedidoItem, PesquisaSemanal, PesquisaSemanalItem, Produto, ProdutoRegistrado, Mercado
from app.auth.utils import login_obrigatorio

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@bp.get("/logins")
@login_obrigatorio(perfis=["admin"])
def listar_logins():
    """Histórico de acesso — pergunta que o usuário fez diretamente: 'dá
    pra saber quem tem usado o sistema?'. Os 200 mais recentes bastam pra
    uma tela de auditoria; isso aqui não substitui um log de verdade (ex.:
    CloudWatch/Sentry) se um dia precisar de retenção maior."""
    logins = LoginLog.query.filter_by(empresa_id=g.empresa_id).order_by(LoginLog.logado_em.desc()).limit(200).all()
    return jsonify(
        [
            {
                "id": log.id,
                "nome": log.nome,
                "perfil": log.perfil,
                "ip": log.ip,
                "logadoEm": log.logado_em.isoformat() if log.logado_em else None,
            }
            for log in logins
        ]
    )


@bp.get("/operadores")
@login_obrigatorio(perfis=["admin"])
def listar_operadores():
    """Lista de nomes distintos de operador pra alimentar o seletor da tela
    'Atividade por Operador' (2026-10-13, pedido do usuário: 'o admin
    consegue monitorar os registros de cada operador?'). IMPORTANTE: esse
    nome é o texto livre digitado no Setup Rápido (`quickSetup.operatorName`
    no frontend) — NÃO é necessariamente o `nome` de login (`Usuario`).
    Hoje nada no app amarra os dois; um operador pode logar como 'joao' e
    digitar 'João da Silva' no Setup Rápido. Por isso a lista vem da união
    dos nomes que de fato aparecem nos registros, não da tabela de usuários
    — é o que reflete a realidade do que foi gravado."""
    empresa_id = g.empresa_id

    nomes: set[str] = set()
    for modelo, campo in (
        (ProdutoRegistrado, ProdutoRegistrado.operator_name),
        (Movimentacao, Movimentacao.operador),
        (PesquisaSemanal, PesquisaSemanal.operador),
        (Avaria, Avaria.operador),
        (PedidoItem, PedidoItem.operador),
    ):
        valores = modelo.query.filter_by(empresa_id=empresa_id).with_entities(campo).distinct().all()
        nomes.update(v[0] for v in valores if v[0] and v[0].strip())

    return jsonify(sorted(nomes, key=str.lower))


@bp.get("/atividade-operador")
@login_obrigatorio(perfis=["admin"])
def atividade_operador():
    """Linha do tempo de tudo que foi registrado, juntando os 5 tipos de
    registro que carregam nome de operador (produto registrado,
    movimentação, pesquisa semanal fechada, avaria, giro/"qtd. a pedir") —
    pedido do usuário pra conseguir monitorar o que cada operador andou
    fazendo.

    2026-10-14 (PDF "Questionamento Técnico", seção 1 — "o admin consegue
    visualizar TODOS os registros de QUALQUER usuário?"): `operador` deixou
    de ser obrigatório. Sem ele, devolve o consolidado de TODOS os
    operadores desta empresa (visão global do admin). Com ele, filtra só
    aquele operador — igual antes. Também aceita, opcionalmente, `mercado`
    (slug — ver GET /api/mercados) e um período via `inicio`/`fim` (datas
    'YYYY-MM-DD', filtro aplicado sobre a data de cada evento). Qualquer
    combinação dos três filtros pode ser usada ao mesmo tempo."""
    operador = (request.args.get("operador") or "").strip()
    mercado_slug = (request.args.get("mercado") or "").strip()
    data_inicio = (request.args.get("inicio") or "").strip()
    data_fim = (request.args.get("fim") or "").strip()

    empresa_id = g.empresa_id
    eventos = []

    # Movimentação/Pesquisa Semanal/Avaria/PedidoItem só guardam
    # `mercado_slug` (ver comentário em Movimentacao, models.py) — resolve
    # pro nome de verdade aqui, numa consulta só, em vez de mostrar o slug
    # cru pro admin. ProdutoRegistrado é o único que guarda o nome livre
    # direto (`nome_cliente`, módulo mais antigo, sem FK formal pra
    # Mercado) — pra filtrar ele por mercado, resolve o slug escolhido pro
    # nome correspondente e compara por nome.
    nomes_por_slug = {m.slug: m.nome for m in Mercado.query.filter_by(empresa_id=empresa_id).all()}
    nome_mercado_filtro = nomes_por_slug.get(mercado_slug) if mercado_slug else None

    consulta_produtos = ProdutoRegistrado.query.filter_by(empresa_id=empresa_id, deletado=False)
    if operador:
        consulta_produtos = consulta_produtos.filter_by(operator_name=operador)
    if nome_mercado_filtro:
        consulta_produtos = consulta_produtos.filter_by(nome_cliente=nome_mercado_filtro)
    for p in consulta_produtos.all():
        eventos.append(
            {
                "tipo": "produto_registrado",
                "data": p.registration_date or (p.atualizado_em.isoformat() if p.atualizado_em else None),
                "descricao": p.description,
                "codigoEAN": p.ean_code,
                "quantidade": p.quantity,
                "mercado": p.nome_cliente,
                "operador": p.operator_name,
            }
        )

    consulta_mov = Movimentacao.query.filter_by(empresa_id=empresa_id)
    if operador:
        consulta_mov = consulta_mov.filter_by(operador=operador)
    if mercado_slug:
        consulta_mov = consulta_mov.filter_by(mercado_slug=mercado_slug)
    for m in consulta_mov.all():
        eventos.append(
            {
                "tipo": "movimentacao",
                "data": m.data.isoformat() if m.data else None,
                "movimentoTipo": m.tipo,
                "motivo": m.motivo,
                "codigoEAN": m.codigo_ean,
                "quantidade": m.quantidade,
                "mercado": nomes_por_slug.get(m.mercado_slug, m.mercado_slug),
                "operador": m.operador,
            }
        )

    consulta_pesquisas = PesquisaSemanal.query.filter_by(empresa_id=empresa_id)
    if operador:
        consulta_pesquisas = consulta_pesquisas.filter_by(operador=operador)
    if mercado_slug:
        consulta_pesquisas = consulta_pesquisas.filter_by(mercado_slug=mercado_slug)
    for ps in consulta_pesquisas.all():
        eventos.append(
            {
                "tipo": "pesquisa_semanal",
                "data": ps.fechada_em.isoformat() if ps.fechada_em else None,
                "cicloId": ps.ciclo_id,
                "mercado": nomes_por_slug.get(ps.mercado_slug, ps.mercado_slug),
                "quantidadeItens": len(ps.itens),
                "operador": ps.operador,
            }
        )

    consulta_avarias = Avaria.query.filter_by(empresa_id=empresa_id)
    if operador:
        consulta_avarias = consulta_avarias.filter_by(operador=operador)
    if mercado_slug:
        consulta_avarias = consulta_avarias.filter_by(mercado_slug=mercado_slug)
    for a in consulta_avarias.all():
        eventos.append(
            {
                "tipo": "avaria",
                "data": a.registrado_em.isoformat() if a.registrado_em else None,
                "descricao": a.descricao,
                "codigoEAN": a.codigo_ean,
                "quantidade": a.quantidade,
                "motivo": a.motivo,
                "mercado": nomes_por_slug.get(a.mercado_slug, a.mercado_slug),
                "operador": a.operador,
            }
        )

    # Giro Registrado / "Qtd. a pedir" (PedidoItem) — faltava aqui, pedido
    # explícito do PDF do usuário: a visão de atividade do admin ainda não
    # incluía esse tipo de registro.
    consulta_pedidos = PedidoItem.query.filter_by(empresa_id=empresa_id)
    if operador:
        consulta_pedidos = consulta_pedidos.filter_by(operador=operador)
    if mercado_slug:
        consulta_pedidos = consulta_pedidos.filter_by(mercado_slug=mercado_slug)
    pedidos = consulta_pedidos.all()
    eans_pedidos = [pi.codigo_ean for pi in pedidos]
    produtos_por_ean = (
        {pr.codigo_ean: pr for pr in Produto.query.filter_by(empresa_id=empresa_id).filter(Produto.codigo_ean.in_(eans_pedidos))}
        if eans_pedidos
        else {}
    )
    for pi in pedidos:
        produto = produtos_por_ean.get(pi.codigo_ean)
        eventos.append(
            {
                "tipo": "pedido_quantidade",
                "data": pi.atualizado_em.isoformat() if pi.atualizado_em else None,
                "descricao": produto.descricao if produto else None,
                "codigoEAN": pi.codigo_ean,
                "quantidade": pi.quantidade,
                "quantityType": pi.quantity_type,
                "cicloId": pi.ciclo_id,
                "mercado": nomes_por_slug.get(pi.mercado_slug, pi.mercado_slug),
                "operador": pi.operador,
            }
        )

    # Período — comparação simples de string ISO (funciona porque todo
    # `data` aqui é 'YYYY-MM-DD...' ou None); `fim` é inclusivo até o fim
    # do dia.
    if data_inicio:
        eventos = [e for e in eventos if e["data"] and e["data"] >= data_inicio]
    if data_fim:
        eventos = [e for e in eventos if e["data"] and e["data"] <= f"{data_fim}T23:59:59.999999"]

    eventos.sort(key=lambda e: e["data"] or "", reverse=True)

    return jsonify(
        {
            "operador": operador or None,
            "mercado": mercado_slug or None,
            "totalEventos": len(eventos),
            "eventos": eventos,
        }
    )


@bp.post("/logs/limpar")
@login_obrigatorio(perfis=["admin"])
def limpar_logs():
    """Apaga DEFINITIVAMENTE o histórico operacional (avarias, movimentações,
    pesquisas semanais) desta empresa — pedido explícito do usuário pra usar
    depois de já ter feito um backup e querer 'deixar algo mais atual
    rodando'. NÃO mexe no saldo atual de estoque (`SaldoEstoque`) nem no
    catálogo/mercados/produtos registrados — isso aqui é só o LOG/histórico,
    o estado atual continua intacto. Exige confirmação explícita no corpo
    pra reduzir o risco de um clique acidental apagar tudo."""
    dados = request.get_json(silent=True) or {}
    if dados.get("confirmar") != "LIMPAR":
        return jsonify({"erro": 'Confirmação necessária: envie {"confirmar": "LIMPAR"} no corpo da requisição.'}), 400

    empresa_id = g.empresa_id

    pesquisa_ids = [p.id for p in PesquisaSemanal.query.filter_by(empresa_id=empresa_id).with_entities(PesquisaSemanal.id).all()]
    itens_removidos = 0
    if pesquisa_ids:
        # Itens primeiro (FK pra pesquisa) — bulk delete não dispara o
        # cascade do relationship, então precisa ser explícito aqui.
        itens_removidos = (
            PesquisaSemanalItem.query.filter(PesquisaSemanalItem.pesquisa_id.in_(pesquisa_ids)).delete(synchronize_session=False)
        )

    removidos = {
        "avarias": Avaria.query.filter_by(empresa_id=empresa_id).delete(synchronize_session=False),
        "movimentacoes": Movimentacao.query.filter_by(empresa_id=empresa_id).delete(synchronize_session=False),
        "pesquisasSemanaisItens": itens_removidos,
        "pesquisasSemanais": PesquisaSemanal.query.filter_by(empresa_id=empresa_id).delete(synchronize_session=False),
    }

    db.session.commit()

    return jsonify({"status": "logs_limpos", "removidos": removidos})
