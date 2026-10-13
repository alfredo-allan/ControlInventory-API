import json

from flask import Blueprint, request, jsonify, g

from app.extensions import db
from app.models import FilaSyncLog
from app.auth.utils import login_obrigatorio
from app.sync.handlers import HANDLERS

bp = Blueprint("sync", __name__, url_prefix="/api")


@bp.post("/sync")
@login_obrigatorio()
def sincronizar():
    """Endpoint único de ingestão — é o que `syncQueue.ts` vai chamar no
    lugar de `enviarParaServidor` (hoje simulado) assim que o frontend
    apontar pra esse backend. Recebe {id, tipo, payload, criadoEm}, exatamente
    o formato de um ItemFilaSync do cliente.

    Idempotente por `id`: se o mesmo item chegar duas vezes (retry do
    cliente depois de uma resposta perdida), a segunda chamada só confirma
    de volta sem reprocessar — essencial numa fila offline-first, onde
    "enviei mas não sei se chegou" é o caso comum, não a exceção.
    """
    dados = request.get_json(silent=True) or {}
    item_id = dados.get("id")
    tipo = dados.get("tipo")
    payload = dados.get("payload")

    if not item_id or not tipo or payload is None:
        return jsonify({"erro": "Payload inválido — esperado {id, tipo, payload}."}), 400

    existente = FilaSyncLog.query.get(item_id)
    if existente and existente.processado:
        return jsonify({"status": "ja_processado", "id": item_id})

    handler = HANDLERS.get(tipo)
    if not handler:
        return jsonify({"erro": f"Tipo de ação desconhecido: {tipo}"}), 400

    log = existente or FilaSyncLog(id=item_id, empresa_id=g.empresa_id, tipo=tipo, payload_json=json.dumps(payload))
    if not existente:
        db.session.add(log)

    try:
        handler(payload)
        log.processado = True
        log.erro = None
        db.session.commit()
    except Exception as exc:  # noqa: BLE001 — queremos capturar qualquer erro de handler e devolver 500 controlado
        db.session.rollback()
        log.erro = str(exc)
        db.session.add(log)
        db.session.commit()
        return jsonify({"erro": "Falha ao processar a ação.", "detalhe": str(exc)}), 500

    return jsonify({"status": "sincronizado", "id": item_id})


@bp.get("/health")
def health():
    return jsonify({"status": "ok"})
