# Instâncias de extensão separadas em módulo próprio — padrão Flask pra
# evitar import circular entre app/__init__.py e os models/blueprints que
# precisam de `db`.

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS

db = SQLAlchemy()
migrate = Migrate()
cors = CORS()
