# Popula o banco pela primeira vez — mesmo catálogo seed que o frontend já
# usava (`client/src/data/frutap.json`, copiado pra seed_data/ aqui) e cria
# o primeiro usuário Administrador.
#
# Uso:
#   python seed.py                       # usa a senha padrão de dev (TROQUE em produção!)
#   python seed.py --admin-senha "..."    # define a senha do admin na hora
#
# Rodar de novo é seguro (idempotente): produtos/mercados existentes só têm
# a descrição/nome sincronizados, e o admin não é duplicado se já existir.

import argparse
import json
import os

from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import Empresa, Produto, Mercado, Usuario

CAMINHO_FRUTAP_JSON = os.path.join(os.path.dirname(__file__), "seed_data", "frutap.json")


def seed(admin_senha: str) -> None:
    app = create_app()
    with app.app_context():
        # As tabelas já devem existir via `flask db upgrade` (ver README.md —
        # é o caminho real, com histórico de migração, que roda tanto local
        # quanto no PythonAnywhere). `db.create_all()` só preenche o que
        # faltar, pra esse script nunca falhar se alguém esquecer o upgrade
        # num banco novo — mas NÃO substitui rodar as migrations de verdade.
        db.create_all()

        empresa_slug = app.config["EMPRESA_PADRAO_SLUG"]
        empresa = Empresa.query.filter_by(slug=empresa_slug).first()
        if not empresa:
            empresa = Empresa(slug=empresa_slug, nome=empresa_slug.capitalize())
            db.session.add(empresa)
            db.session.flush()
            print(f"Empresa '{empresa_slug}' criada.")

        with open(CAMINHO_FRUTAP_JSON, encoding="utf-8") as f:
            catalogo = json.load(f)

        produtos_criados = 0
        for ean, produto in catalogo["produtos"].items():
            existente = Produto.query.filter_by(empresa_id=empresa.id, codigo_ean=ean).first()
            if existente:
                existente.descricao = produto["descricao"]
                continue
            db.session.add(
                Produto(
                    empresa_id=empresa.id,
                    codigo_ean=ean,
                    descricao=produto["descricao"],
                    marca=produto.get("marca", "Frutap"),
                    categoria=produto.get("categoria", ""),
                    unidade=produto.get("unidade", "unidade"),
                    validade_media_dias=produto.get("validadeMediaDias", 15),
                )
            )
            produtos_criados += 1

        mercados_criados = 0
        for slug, mercado in catalogo["mercados"].items():
            existente = Mercado.query.filter_by(empresa_id=empresa.id, slug=slug).first()
            if existente:
                existente.nome = mercado["nome"]
                continue
            endereco = mercado.get("endereco", {})
            db.session.add(
                Mercado(
                    empresa_id=empresa.id,
                    slug=slug,
                    nome=mercado["nome"],
                    endereco_rua=endereco.get("rua", ""),
                    endereco_bairro=endereco.get("bairro", ""),
                    endereco_cidade=endereco.get("cidade", ""),
                    endereco_estado=endereco.get("estado", ""),
                )
            )
            mercados_criados += 1

        admin_nome = "admin"
        if not Usuario.query.filter_by(empresa_id=empresa.id, nome=admin_nome).first():
            db.session.add(
                Usuario(
                    empresa_id=empresa.id,
                    nome=admin_nome,
                    perfil="admin",
                    senha_hash=generate_password_hash(admin_senha),
                )
            )
            print(f"Usuário admin '{admin_nome}' criado — ANOTE a senha usada, ela não é mostrada de novo.")

        db.session.commit()
        print(f"Seed concluído: {produtos_criados} produtos novos, {mercados_criados} mercados novos.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-senha", default=os.environ.get("ADMIN_SEED_SENHA", "troque-essa-senha"))
    args = parser.parse_args()
    seed(args.admin_senha)
