from flask import Blueprint, request, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db
from app.models import Usuario, Empresa, LoginLog
from app.auth.utils import gerar_token, login_obrigatorio

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@bp.post("/login")
def login():
    dados = request.get_json(silent=True) or {}
    nome = (dados.get("nome") or "").strip()
    senha = dados.get("senha") or ""

    if not nome or not senha:
        return jsonify({"erro": "Informe nome e senha."}), 400

    usuario = Usuario.query.filter_by(nome=nome).first()
    if not usuario or not check_password_hash(usuario.senha_hash, senha):
        return jsonify({"erro": "Nome ou senha incorretos."}), 401

    token = gerar_token(usuario)

    # Auditoria simples — uma linha por login bem-sucedido, pro admin
    # conseguir ver (GET /api/admin/logins) quem tem usado o sistema e
    # quando. `nome`/`perfil` gravados como snapshot (não é só o FK) pra o
    # histórico continuar legível mesmo que o usuário mude de nome/perfil
    # depois. Falha ao gravar isso não pode derrubar o login em si.
    try:
        db.session.add(
            LoginLog(
                empresa_id=usuario.empresa_id,
                usuario_id=usuario.id,
                nome=usuario.nome,
                perfil=usuario.perfil,
                ip=request.headers.get("X-Forwarded-For", request.remote_addr),
            )
        )
        db.session.commit()
    except Exception:  # noqa: BLE001 — auditoria nunca pode bloquear o login
        db.session.rollback()

    return jsonify({
        "token": token,
        "usuario": {"id": usuario.id, "nome": usuario.nome, "perfil": usuario.perfil, "empresaId": usuario.empresa_id},
    })


@bp.get("/me")
@login_obrigatorio()
def me():
    usuario = Usuario.query.get_or_404(g.usuario_id)
    return jsonify({"id": usuario.id, "nome": usuario.nome, "perfil": usuario.perfil, "empresaId": usuario.empresa_id})


@bp.get("/usuarios")
@login_obrigatorio(perfis=["admin"])
def listar_usuarios():
    """Lista os usuários da empresa — admin-only, pra tela de 'Criar
    usuário' no Admin (2026-10-13) conseguir mostrar quem já tem acesso
    antes de criar mais um. Nunca devolve `senha_hash`."""
    usuarios = Usuario.query.filter_by(empresa_id=g.empresa_id).order_by(Usuario.criado_em.desc()).all()
    return jsonify([
        {"id": u.id, "nome": u.nome, "perfil": u.perfil, "criadoEm": u.criado_em.isoformat() if u.criado_em else None}
        for u in usuarios
    ])


@bp.post("/usuarios")
@login_obrigatorio(perfis=["admin"])
def criar_usuario():
    """Só um admin pode criar outro usuário/operador — RBAC básico. O
    primeiro usuário admin de cada empresa é criado pelo `seed.py`, não por
    essa rota (evita o problema de 'ovo e galinha' de precisar de um admin
    pra criar o primeiro admin)."""
    dados = request.get_json(silent=True) or {}
    nome = (dados.get("nome") or "").strip()
    senha = dados.get("senha") or ""
    perfil = dados.get("perfil", "operador")

    if not nome or not senha:
        return jsonify({"erro": "Informe nome e senha."}), 400
    if len(senha) < 6:
        return jsonify({"erro": "A senha precisa ter pelo menos 6 caracteres."}), 400
    if perfil not in ("admin", "operador"):
        return jsonify({"erro": "Perfil deve ser 'admin' ou 'operador'."}), 400

    if Usuario.query.filter_by(empresa_id=g.empresa_id, nome=nome).first():
        return jsonify({"erro": "Já existe um usuário com esse nome nessa empresa."}), 409

    usuario = Usuario(empresa_id=g.empresa_id, nome=nome, perfil=perfil, senha_hash=generate_password_hash(senha))
    db.session.add(usuario)
    db.session.commit()

    return jsonify({"id": usuario.id, "nome": usuario.nome, "perfil": usuario.perfil}), 201
