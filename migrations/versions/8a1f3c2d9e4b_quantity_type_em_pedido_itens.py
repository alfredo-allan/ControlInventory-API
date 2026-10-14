"""quantity_type em pedido_itens

Revision ID: 8a1f3c2d9e4b
Revises: 57d71f7e6461
Create Date: 2026-10-14 17:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8a1f3c2d9e4b'
down_revision = '57d71f7e6461'
branch_labels = None
depends_on = None


def upgrade():
    # Mesmo padrão da migração 50f042a06ac5 (quantity_type em avarias e
    # pesquisa semanal): coluna NOT NULL nova numa tabela que já pode ter
    # linhas em produção (pedido_itens criada em 57d71f7e6461) precisa de
    # `server_default` pra preencher as linhas existentes — senão o SQLite
    # recusa o ADD COLUMN. Remove o default a nível de banco logo depois; o
    # default de verdade continua sendo o do SQLAlchemy/Python (models.py).
    with op.batch_alter_table('pedido_itens', schema=None) as batch_op:
        batch_op.add_column(sa.Column('quantity_type', sa.String(length=20), nullable=False, server_default='unidade'))

    with op.batch_alter_table('pedido_itens', schema=None) as batch_op:
        batch_op.alter_column('quantity_type', server_default=None)


def downgrade():
    with op.batch_alter_table('pedido_itens', schema=None) as batch_op:
        batch_op.drop_column('quantity_type')
