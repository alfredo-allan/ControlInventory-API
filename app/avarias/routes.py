from flask import Blueprint, jsonify, g

from app.models import Avaria
from app.auth.utils import login_obrigatorio

bp = Blueprint("avarias", __name__, url_prefix="/api/avarias")


@bp.get("")
@login_obrigatorio()
def listar_avarias():
    """Leitura aberta pros dois perfis — é o mesmo nível de sensibilidade
    de `GET /api/produtos-registrados` (cada operador vendo o que foi
    registrado, inclusive por outros aparelhos/operadores via pull-sync).
    O relatório agregado de perdas (`/api/relatorios/perdas.docx`) é que
    fica admin-only, não esta listagem crua."""
    avarias = Avaria.query.filter_by(empresa_id=g.empresa_id).order_by(Avaria.registrado_em.desc()).all()
    return jsonify(
        [
            {
                "id": a.id,
                "mercadoId": a.mercado_slug,
                "codigoEAN": a.codigo_ean,
                "descricao": a.descricao,
                "quantidade": a.quantidade,
                "motivo": a.motivo,
                "operador": a.operador,
                "nomeCliente": a.nome_cliente,
                "enderecoCliente": a.endereco_cliente,
                "registradoEm": a.registrado_em.isoformat() if a.registrado_em else None,
            }
            for a in avarias
        ]
    )
