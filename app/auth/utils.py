# JWT simples com PyJWT puro — sem flask-jwt-extended pra manter a
# dependência mínima (o deploy é no PythonAnywhere, onde instalar menos
# pacotes = menos chance de algo dar errado no virtualenv deles).

import jwt
from datetime import datetime, timezone
from functools import wraps
from flask import request, jsonify, current_app, g

from app.models import Usuario


def gerar_token(usuario: Usuario) -> str:
    agora = datetime.now(timezone.utc)
    payload = {
        # PyJWT >= 2.10 exige que "sub" (RFC 7519) seja string — por isso o
        # str() aqui. Do lado da leitura (wrapper abaixo), convertemos de
        # volta pra int antes de guardar em g.usuario_id.
        "sub": str(usuario.id),
        "empresa_id": usuario.empresa_id,
        "perfil": usuario.perfil,
        "iat": agora,
        "exp": agora + current_app.config["JWT_EXPIRATION"],
    }
    return jwt.encode(payload, current_app.config["SECRET_KEY"], algorithm="HS256")


def _extrair_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer "):]
    return None


def login_obrigatorio(perfis: list[str] | None = None):
    """Decorator — exige um JWT válido no header Authorization. Se `perfis`
    for passado (ex: ['admin']), também exige que o usuário tenha um desses
    perfis — é a base do RBAC da Fase 3, já pronta pro backend mesmo antes
    da UI de login existir no frontend."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            token = _extrair_token()
            if not token:
                return jsonify({"erro": "Token ausente. Envie Authorization: Bearer <token>."}), 401
            try:
                payload = jwt.decode(token, current_app.config["SECRET_KEY"], algorithms=["HS256"])
            except jwt.ExpiredSignatureError:
                return jsonify({"erro": "Token expirado, faça login novamente."}), 401
            except jwt.InvalidTokenError:
                return jsonify({"erro": "Token inválido."}), 401

            if perfis and payload.get("perfil") not in perfis:
                return jsonify({"erro": "Sem permissão pra essa ação."}), 403

            g.usuario_id = int(payload["sub"])
            g.empresa_id = payload["empresa_id"]
            g.perfil = payload["perfil"]
            return fn(*args, **kwargs)

        return wrapper

    return decorator
