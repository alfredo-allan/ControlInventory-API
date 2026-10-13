# App factory — padrão Flask recomendado (em vez de `app = Flask(__name__)`
# solto num módulo), porque facilita testar com configs diferentes e evita
# import circular entre extensões/models/blueprints.

import os

from flask import Flask, send_from_directory

from app.config import get_config
from app.extensions import db, migrate, cors


def create_app(config_class=None):
    app = Flask(__name__)
    app.config.from_object(config_class or get_config())

    db.init_app(app)
    migrate.init_app(app, db)
    cors.init_app(app, resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}})

    # Import dos models aqui dentro (não no topo do arquivo) é necessário
    # pro Flask-Migrate enxergar as tabelas na hora de gerar migrações.
    from app import models  # noqa: F401

    from app.auth.routes import bp as auth_bp
    from app.sync.routes import bp as sync_bp
    from app.catalogo.routes import bp as catalogo_bp
    from app.estoque.routes import bp as estoque_bp
    from app.metricas.routes import bp as metricas_bp
    from app.avarias.routes import bp as avarias_bp
    from app.admin.routes import bp as admin_bp
    from app.relatorios.routes import bp as relatorios_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(sync_bp)
    app.register_blueprint(catalogo_bp)
    app.register_blueprint(estoque_bp)
    app.register_blueprint(metricas_bp)
    app.register_blueprint(avarias_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(relatorios_bp)

    # "Tudo num site só" (2026-10-13) — registrada só se a pasta do build do
    # frontend (FRONTEND_DIST_DIR) existir de verdade. Em dev local ela não
    # existe (o frontend roda via `vite dev` na 5173, separado), então essa
    # rota simplesmente não entra — nenhum comportamento muda pra quem já
    # está trabalhando localmente. Em produção (PythonAnywhere), a pasta
    # existe porque o passo de deploy manda copiar `client/dist` pra lá antes
    # de recarregar o web app (ver claude/backend-api-pythonanywhere.md).
    frontend_dist = app.config["FRONTEND_DIST_DIR"]
    if os.path.isdir(frontend_dist):
        @app.route("/", defaults={"caminho": ""})
        @app.route("/<path:caminho>")
        def servir_frontend(caminho):
            # Qualquer caminho que bata com um arquivo real do build (JS,
            # CSS, imagens, etc.) é servido direto. Qualquer outro caminho —
            # inclusive rotas da SPA tipo /admin, /avarias, /farejar — cai no
            # index.html, pro React Router (wouter) do lado do cliente
            # decidir o que renderizar. Sem isso, um F5 numa URL que não seja
            # "/" devolveria 404 do Flask em vez de carregar o app.
            #
            # /api/* nunca deveria chegar aqui (os blueprints acima já
            # respondem qualquer rota real deles) — se chegou, é um endpoint
            # que não existe, e o certo é 404 de verdade, não devolver HTML
            # pro cliente esperando JSON.
            if caminho.startswith("api/"):
                return {"erro": "Endpoint não encontrado."}, 404
            caminho_completo = os.path.join(frontend_dist, caminho) if caminho else None
            if caminho and os.path.isfile(caminho_completo):
                return send_from_directory(frontend_dist, caminho)
            return send_from_directory(frontend_dist, "index.html")

    return app
