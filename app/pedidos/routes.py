from flask import Blueprint, jsonify, g

from app.models import PedidoItem
from app.auth.utils import login_obrigatorio

bp = Blueprint("pedidos", __name__, url_prefix="/api/pedidos")


@bp.get("")
@login_obrigatorio()
def listar_pedidos():
    """"Quantidade a pedir" / Giro Registrado — leitura aberta pros dois
    perfis, mesmo nível de sensibilidade de /api/avarias (qualquer operador
    vendo o que foi preenchido, inclusive por outro aparelho, via
    pull-sync). Devolve TODOS os itens da empresa (todo mercado, todo
    ciclo) — mesmo padrão não-filtrado de GET /api/avarias; o volume é
    pequeno (só existe linha quando alguém de fato preenche uma
    quantidade) e o cliente já filtra por mercado+ciclo localmente."""
    itens = PedidoItem.query.filter_by(empresa_id=g.empresa_id).order_by(PedidoItem.atualizado_em.desc()).all()
    return jsonify(
        [
            {
                "id": p.id,
                "mercadoId": p.mercado_slug,
                "codigoEAN": p.codigo_ean,
                "cicloId": p.ciclo_id,
                "quantidade": p.quantidade,
                "quantityType": p.quantity_type,
                "operador": p.operador,
                "atualizadoEm": p.atualizado_em.isoformat() if p.atualizado_em else None,
            }
            for p in itens
        ]
    )
