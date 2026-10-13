from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, g, request
from sqlalchemy import func

from app.extensions import db
from app.models import Movimentacao, Mercado
from app.auth.utils import login_obrigatorio

bp = Blueprint("metricas", __name__, url_prefix="/api/metricas")


def calcular_perdas_por_mercado(empresa_id: int, dias: int) -> dict:
    """Base do Painel de Métricas da Fase 3 (visão do Administrador) — soma
    avaria/vencimento por mercado numa janela de dias. Mesma lógica de
    `calcularResumoPerdas` em estoqueAnalytics.ts, só que agregada no banco
    em vez de em memória no navegador. Extraída numa função própria (antes
    vivia só dentro da rota) pra ser reaproveitada também pelo relatório
    .docx (`app/relatorios/routes.py`) — mesmo número, duas formas de ver."""
    desde = datetime.now(timezone.utc) - timedelta(days=dias)

    linhas = (
        db.session.query(
            Movimentacao.mercado_slug,
            Movimentacao.motivo,
            func.sum(Movimentacao.quantidade).label("total"),
        )
        .filter(
            Movimentacao.empresa_id == empresa_id,
            Movimentacao.tipo == "saida",
            Movimentacao.motivo.in_(["avaria", "vencimento", "venda"]),
            Movimentacao.data >= desde,
        )
        .group_by(Movimentacao.mercado_slug, Movimentacao.motivo)
        .all()
    )

    mercados = {m.slug: m.nome for m in Mercado.query.filter_by(empresa_id=empresa_id).all()}

    por_mercado: dict[str, dict] = {}
    for mercado_slug, motivo, total in linhas:
        bucket = por_mercado.setdefault(
            mercado_slug, {"mercadoId": mercado_slug, "mercadoNome": mercados.get(mercado_slug, mercado_slug), "avaria": 0, "vencimento": 0, "venda": 0}
        )
        bucket[motivo] = int(total)

    resultado = []
    for bucket in por_mercado.values():
        total_saidas = bucket["avaria"] + bucket["vencimento"] + bucket["venda"]
        total_perdas = bucket["avaria"] + bucket["vencimento"]
        bucket["totalPerdas"] = total_perdas
        bucket["totalSaidas"] = total_saidas
        bucket["percentual"] = round((total_perdas / total_saidas) * 100, 1) if total_saidas > 0 else 0
        resultado.append(bucket)

    resultado.sort(key=lambda b: b["totalPerdas"], reverse=True)
    return {"diasJanela": dias, "mercados": resultado}


@bp.get("/perdas")
@login_obrigatorio(perfis=["admin"])
def perdas_por_mercado():
    dias = int(request.args.get("dias", 30))
    return jsonify(calcular_perdas_por_mercado(g.empresa_id, dias))
