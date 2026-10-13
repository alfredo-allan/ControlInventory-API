from flask import Blueprint, jsonify, g

from app.extensions import db
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
                "quantityType": a.quantity_type,
                "motivo": a.motivo,
                "operador": a.operador,
                "nomeCliente": a.nome_cliente,
                "enderecoCliente": a.endereco_cliente,
                "registradoEm": a.registrado_em.isoformat() if a.registrado_em else None,
            }
            for a in avarias
        ]
    )


@bp.delete("/<avaria_id>")
@login_obrigatorio(perfis=["admin"])
def excluir_avaria(avaria_id):
    """Exclui um registro de avaria individual — admin-only (2026-10-13,
    pedido do usuário depois de clicar na badge '1x un' esperando que ela
    excluísse o registro, o que ela não fazia — era só um indicador visual
    de quantidade). Decisão deliberada, fechada via AskUserQuestion: isso
    NÃO devolve a quantidade ao estoque nem mexe na `Movimentacao`
    espelhada (tipo='saida', motivo='avaria') que já foi lançada — só
    remove o registro/log da avaria em si. O abatimento já feito continua
    valendo, igual a filosofia do resto do app ('o estoque reflete o que
    realmente aconteceu'); é diferente de 'Limpar todos os logs'
    (POST /api/admin/logs/limpar), que é uma limpeza em massa."""
    avaria = Avaria.query.filter_by(id=avaria_id, empresa_id=g.empresa_id).first()
    if not avaria:
        return jsonify({"erro": "Avaria não encontrada."}), 404

    db.session.delete(avaria)
    db.session.commit()

    return jsonify({"ok": True})
