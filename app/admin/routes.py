# Rotas administrativas sensíveis — tudo aqui é @login_obrigatorio(perfis=
# ["admin"]). Agrupadas num blueprint próprio (em vez de espalhadas em
# catalogo/estoque) porque são operações de outra natureza: auditoria e
# limpeza de dados, não leitura/escrita operacional do dia a dia.

from flask import Blueprint, request, jsonify, g

from app.extensions import db
from app.models import LoginLog, Avaria, Movimentacao, PesquisaSemanal, PesquisaSemanalItem, ProdutoRegistrado, Mercado
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
    ):
        valores = modelo.query.filter_by(empresa_id=empresa_id).with_entities(campo).distinct().all()
        nomes.update(v[0] for v in valores if v[0] and v[0].strip())

    return jsonify(sorted(nomes, key=str.lower))


@bp.get("/atividade-operador")
@login_obrigatorio(perfis=["admin"])
def atividade_operador():
    """Linha do tempo de tudo que um operador fez, juntando os 4 tipos de
    registro que carregam o nome dele (produto registrado, movimentação,
    pesquisa semanal fechada, avaria) — pedido do usuário pra conseguir
    monitorar o que cada operador andou fazendo. Sem filtro de `operador`,
    devolve 400 em vez de trazer tudo misturado de todo mundo (isso já
    existe espalhado nas rotas de leitura normais)."""
    operador = (request.args.get("operador") or "").strip()
    if not operador:
        return jsonify({"erro": "Informe o parâmetro 'operador'."}), 400

    empresa_id = g.empresa_id
    eventos = []

    # Movimentação/Pesquisa Semanal/Avaria só guardam `mercado_slug` (ver
    # comentário em Movimentacao, models.py) — resolve pro nome de verdade
    # aqui, numa consulta só, em vez de mostrar o slug cru pro admin.
    nomes_por_slug = {m.slug: m.nome for m in Mercado.query.filter_by(empresa_id=empresa_id).all()}

    for p in ProdutoRegistrado.query.filter_by(empresa_id=empresa_id, operator_name=operador, deletado=False).all():
        eventos.append(
            {
                "tipo": "produto_registrado",
                "data": p.registration_date or (p.atualizado_em.isoformat() if p.atualizado_em else None),
                "descricao": p.description,
                "codigoEAN": p.ean_code,
                "quantidade": p.quantity,
                "mercado": p.nome_cliente,
            }
        )

    for m in Movimentacao.query.filter_by(empresa_id=empresa_id, operador=operador).all():
        eventos.append(
            {
                "tipo": "movimentacao",
                "data": m.data.isoformat() if m.data else None,
                "movimentoTipo": m.tipo,
                "motivo": m.motivo,
                "codigoEAN": m.codigo_ean,
                "quantidade": m.quantidade,
                "mercado": nomes_por_slug.get(m.mercado_slug, m.mercado_slug),
            }
        )

    for ps in PesquisaSemanal.query.filter_by(empresa_id=empresa_id, operador=operador).all():
        eventos.append(
            {
                "tipo": "pesquisa_semanal",
                "data": ps.fechada_em.isoformat() if ps.fechada_em else None,
                "cicloId": ps.ciclo_id,
                "mercado": nomes_por_slug.get(ps.mercado_slug, ps.mercado_slug),
                "quantidadeItens": len(ps.itens),
            }
        )

    for a in Avaria.query.filter_by(empresa_id=empresa_id, operador=operador).all():
        eventos.append(
            {
                "tipo": "avaria",
                "data": a.registrado_em.isoformat() if a.registrado_em else None,
                "descricao": a.descricao,
                "codigoEAN": a.codigo_ean,
                "quantidade": a.quantidade,
                "motivo": a.motivo,
                "mercado": nomes_por_slug.get(a.mercado_slug, a.mercado_slug),
            }
        )

    eventos.sort(key=lambda e: e["data"] or "", reverse=True)

    return jsonify({"operador": operador, "totalEventos": len(eventos), "eventos": eventos})


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
