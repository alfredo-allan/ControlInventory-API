# Configuração do app — lê tudo de variáveis de ambiente (nunca hardcoded),
# porque o mesmo código roda em cenários diferentes: local (SQLite, rápido
# de testar) e no PythonAnywhere — SQLite também no plano Free (2026-10-13,
# decisão do usuário: conta é Free, sem MySQL disponível lá), ou MySQL de
# verdade se um dia migrar pro plano Developer (só definir as 4 variáveis
# DB_* abaixo — nenhuma mudança de código necessária pra trocar de um pro
# outro, ver `_monta_url_mysql()`).
#
# No PythonAnywhere, essas variáveis são definidas no arquivo WSGI (ver
# wsgi.py) ou num .env carregado por ele — o painel Web do PythonAnywhere não
# tem uma seção nativa de "variáveis de ambiente" como Heroku/Vercel, então o
# jeito padrão de lá é setar com os.environ[...] direto no WSGI antes de
# importar o app, ou carregar um .env com python-dotenv.

import os
from datetime import timedelta


def _monta_url_mysql() -> str | None:
    """Monta a connection string do MySQL a partir das variáveis separadas
    que o painel "Databases" do PythonAnywhere mostra (host, usuário, senha,
    nome do banco) — evita o usuário ter que montar a URL na mão."""
    host = os.environ.get("DB_HOST")
    user = os.environ.get("DB_USER")
    senha = os.environ.get("DB_PASSWORD")
    nome = os.environ.get("DB_NAME")
    if not all([host, user, senha, nome]):
        return None
    return f"mysql+pymysql://{user}:{senha}@{host}/{nome}?charset=utf8mb4"


class ConfigBase:
    # SECRET_KEY assina os tokens JWT — em produção isso é OBRIGATÓRIO vir de
    # variável de ambiente (ver .env.example). O valor abaixo só existe pra
    # não quebrar `flask db` localmente se alguém esquecer de criar o .env.
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-troque-isso-em-producao")

    SQLALCHEMY_DATABASE_URI = (
        _monta_url_mysql()
        or os.environ.get("DATABASE_URL")
        or "sqlite:///" + os.path.join(os.path.dirname(os.path.dirname(__file__)), "dev.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Token de acesso dura pouco (operador loga de novo com frequência não é
    # problema nesse app); o importante é a FILA OFFLINE não depender de um
    # token ainda válido pra ENFILEIRAR localmente — só pra sincronizar. Se o
    # token expirar enquanto offline, a fila continua crescendo normalmente;
    # o app pode re-logar automaticamente quando a conexão voltar.
    JWT_EXPIRATION = timedelta(days=7)

    # Origens permitidas pra CORS — a SPA (Vite/Vercel) chama essa API de um
    # domínio diferente. Configurável via env pra não precisar mudar código
    # quando o domínio do frontend mudar (preview deploys da Vercel, etc).
    CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",")]

    # Empresa padrão — hoje só existe a Frutap, mas todo o schema já é
    # multi-tenant (empresa_id em tudo) pra quando entrar o segundo cliente.
    EMPRESA_PADRAO_SLUG = os.environ.get("EMPRESA_PADRAO_SLUG", "frutap")

    # Deploy "tudo num site só" (2026-10-13) — o Flask também pode servir o
    # build estático do frontend (client/dist), pro plano free do
    # PythonAnywhere (que só permite UM web app) não precisar de um segundo
    # serviço de hospedagem pro frontend. Pasta relativa à raiz do backend;
    # fica vazia/ausente em dev local (onde o frontend roda via `vite dev`
    # separado, na porta 5173) — nesse caso a rota de SPA simplesmente não é
    # registrada (ver app/__init__.py), sem quebrar nada.
    FRONTEND_DIST_DIR = os.environ.get(
        "FRONTEND_DIST_DIR",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend_dist"),
    )


class ConfigDesenvolvimento(ConfigBase):
    DEBUG = True


class ConfigProducao(ConfigBase):
    DEBUG = False


def get_config():
    ambiente = os.environ.get("FLASK_ENV", "development")
    return ConfigProducao if ambiente == "production" else ConfigDesenvolvimento
