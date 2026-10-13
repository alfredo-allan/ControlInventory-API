from flask import Blueprint, jsonify, g, request

from app.models import Produto, Mercado, SaldoEstoque, Movimentacao, PesquisaSemanal
from app.auth.utils import login_obrigatorio

bp = Blueprint("estoque", __name__, url_prefix="/api")


@bp.get("/estoque/<mercado_slug>")
@login_obrigatorio()
def listar_estoque_do_mercado(mercado_slug: str):
    """Espelha `listarProdutos(mercadoId)` do frontend: TODO o catálogo da
    empresa, com o saldo daquele mercado (0 se nunca foi contado/lançado ali).
    Mesma filosofia que corrigiu o bug da v2 — nenhum produto fica escondido
    de nenhum mercado."""
    mercado = Mercado.query.filter_by(empresa_id=g.empresa_id, slug=mercado_slug).first()
    if not mercado:
        return jsonify({"erro": "Mercado não encontrado."}), 404

    saldos_por_produto = {s.produto_id: s for s in mercado.saldos}
    produtos = Produto.query.filter_by(empresa_id=g.empresa_id).order_by(Produto.descricao).all()

    resultado = []
    for p in produtos:
        saldo = saldos_por_produto.get(p.id)
        resultado.append(
            {
                "codigoEAN": p.codigo_ean,
                "produto": {
                    "descricao": p.descricao,
                    "marca": p.marca,
                    "categoria": p.categoria,
                    "unidade": p.unidade,
                    "validadeMediaDias": p.validade_media_dias,
                    "estoque": {
                        "quantidadeAtual": saldo.quantidade_atual if saldo else 0,
                        "quantidadeMinima": saldo.quantidade_minima if saldo else 0,
                        "precoUnitario": float(saldo.preco_unitario) if saldo and saldo.preco_unitario is not None else None,
                        "atualizadoEm": saldo.atualizado_em.isoformat() if saldo else "",
                    },
                },
            }
        )
    return jsonify(resultado)


@bp.get("/movimentacoes")
@login_obrigatorio()
def listar_movimentacoes():
    mercado_slug = request.args.get("mercado")
    query = Movimentacao.query.filter_by(empresa_id=g.empresa_id)
    if mercado_slug:
        query = query.filter_by(mercado_slug=mercado_slug)

    movimentacoes = query.order_by(Movimentacao.data.desc()).limit(500).all()
    return jsonify(
        [
            {
                "id": m.id,
                "mercadoId": m.mercado_slug,
                "codigoEAN": m.codigo_ean,
                "tipo": m.tipo,
                "motivo": m.motivo,
                "quantidade": m.quantidade,
                "operador": m.operador,
                "data": m.data.isoformat() if m.data else None,
                "cicloId": m.ciclo_id,
            }
            for m in movimentacoes
        ]
    )


@bp.get("/pesquisas-semanais")
@login_obrigatorio()
def listar_pesquisas_semanais():
    mercado_slug = request.args.get("mercado")
    query = PesquisaSemanal.query.filter_by(empresa_id=g.empresa_id)
    if mercado_slug:
        query = query.filter_by(mercado_slug=mercado_slug)

    pesquisas = query.order_by(PesquisaSemanal.fechada_em.desc()).limit(100).all()
    return jsonify(
        [
            {
                "id": p.id,
                "mercadoId": p.mercado_slug,
                "cicloId": p.ciclo_id,
                "operador": p.operador,
                "fechadaEm": p.fechada_em.isoformat() if p.fechada_em else None,
                "itens": [
                    {
                        "codigoEAN": i.codigo_ean,
                        "quantidadeEsperada": i.quantidade_esperada,
                        "quantidadeContada": i.quantidade_contada,
                        "divergencia": i.divergencia,
                    }
                    for i in p.itens
                ],
            }
            for p in pesquisas
        ]
    )
