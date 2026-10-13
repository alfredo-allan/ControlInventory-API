from flask import Blueprint, jsonify, g

from app.models import Produto, Mercado, ProdutoRegistrado
from app.auth.utils import login_obrigatorio

bp = Blueprint("catalogo", __name__, url_prefix="/api")


@bp.get("/produtos")
@login_obrigatorio()
def listar_produtos():
    produtos = Produto.query.filter_by(empresa_id=g.empresa_id).order_by(Produto.descricao).all()
    return jsonify(
        [
            {
                "codigoEAN": p.codigo_ean,
                "descricao": p.descricao,
                "marca": p.marca,
                "categoria": p.categoria,
                "unidade": p.unidade,
                "validadeMediaDias": p.validade_media_dias,
            }
            for p in produtos
        ]
    )


@bp.get("/mercados")
@login_obrigatorio()
def listar_mercados():
    mercados = Mercado.query.filter_by(empresa_id=g.empresa_id).order_by(Mercado.nome).all()
    return jsonify(
        [
            {
                "id": m.slug,
                "nome": m.nome,
                "endereco": {
                    "rua": m.endereco_rua,
                    "bairro": m.endereco_bairro,
                    "cidade": m.endereco_cidade,
                    "estado": m.endereco_estado,
                },
            }
            for m in mercados
        ]
    )


@bp.get("/produtos-registrados")
@login_obrigatorio()
def listar_produtos_registrados():
    """Lista do fluxo de Registrar (itens perecíveis), no mesmo formato
    `Product` que o frontend já usa — é o que fecha a lacuna da tela Farejar
    (e Listar) só enxergarem o que está no localStorage daquele aparelho:
    com isso dá pra puxar o estado de verdade do servidor no load do app."""
    produtos = (
        ProdutoRegistrado.query.filter_by(empresa_id=g.empresa_id, deletado=False)
        .order_by(ProdutoRegistrado.registration_date.desc())
        .all()
    )
    return jsonify(
        [
            {
                "id": p.id,
                "operatorName": p.operator_name,
                "eanCode": p.ean_code,
                "description": p.description,
                "quantity": p.quantity,
                "quantityType": p.quantity_type,
                "expirationDate": p.expiration_date,
                "registrationDate": p.registration_date,
                "imageUrl": p.image_url,
                "nomeCliente": p.nome_cliente,
                "enderecoCliente": p.endereco_cliente,
            }
            for p in produtos
        ]
    )
