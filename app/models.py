# Schema do banco — espelha DE PROPÓSITO os tipos TypeScript que já existem
# no frontend (client/src/lib/estoqueStorage.ts, localStorage.ts,
# syncQueue.ts). Mesmos nomes de campo (em português, snake_case aqui porque
# é Python), mesma forma dos dados — é isso que faz o endpoint /api/sync
# conseguir receber o payload exatamente como o cliente já monta ele, sem
# nenhuma camada de tradução no meio.
#
# Tudo tem `empresa_id` — o app hoje só tem a empresa "frutap", mas o
# isolamento multi-tenant já existia no frontend (EMPRESA_PADRAO) e continua
# aqui, pronto pro segundo cliente/distribuidora sem precisar migrar nada.

import uuid
from datetime import datetime, timezone

from app.extensions import db


def _uuid() -> str:
    return str(uuid.uuid4())


def _agora() -> datetime:
    return datetime.now(timezone.utc)


class Empresa(db.Model):
    __tablename__ = "empresas"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(80), unique=True, nullable=False)  # ex: "frutap"
    nome = db.Column(db.String(200), nullable=False)
    criado_em = db.Column(db.DateTime(timezone=True), default=_agora)

    usuarios = db.relationship("Usuario", back_populates="empresa", lazy="dynamic")
    produtos = db.relationship("Produto", back_populates="empresa", lazy="dynamic")
    mercados = db.relationship("Mercado", back_populates="empresa", lazy="dynamic")


class Usuario(db.Model):
    """Base pro RBAC da Fase 3 (Administrador x Operador) — já criado agora
    porque o endpoint de sync precisa de ALGUMA autenticação, e faz sentido
    que essa autenticação já seja a mesma usada no app depois."""

    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    nome = db.Column(db.String(200), nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)
    perfil = db.Column(db.String(20), nullable=False, default="operador")  # 'admin' | 'operador'
    # Soft delete (2026-10-13, pedido do usuário: admin poder "excluir"
    # usuário sem quebrar o histórico de LoginLog, que referencia usuario_id
    # como obrigatório). Desativar = login passa a recusar esse usuário
    # (ver auth/routes.py), mas toda a auditoria antiga continua intacta.
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    criado_em = db.Column(db.DateTime(timezone=True), default=_agora)

    empresa = db.relationship("Empresa", back_populates="usuarios")

    __table_args__ = (db.UniqueConstraint("empresa_id", "nome", name="uq_usuario_empresa_nome"),)


class Produto(db.Model):
    """Catálogo global por empresa — espelha `CatalogoProduto` /
    `empresa.produtos` em estoqueStorage.ts. Um produto, um registro; nunca
    duplicado por mercado (foi exatamente esse bug, na v1 do frontend, que
    motivou o catálogo global ali — o backend nasce já do jeito certo)."""

    __tablename__ = "produtos"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    codigo_ean = db.Column(db.String(32), nullable=False)
    descricao = db.Column(db.String(300), nullable=False)
    marca = db.Column(db.String(120), default="")
    categoria = db.Column(db.String(120), default="")
    unidade = db.Column(db.String(20), default="unidade")  # 'unidade' | 'caixa'
    validade_media_dias = db.Column(db.Integer, default=15)
    atualizado_em = db.Column(db.DateTime(timezone=True), default=_agora, onupdate=_agora)

    empresa = db.relationship("Empresa", back_populates="produtos")
    saldos = db.relationship("SaldoEstoque", back_populates="produto", lazy="dynamic")

    __table_args__ = (db.UniqueConstraint("empresa_id", "codigo_ean", name="uq_produto_empresa_ean"),)


class Mercado(db.Model):
    """Cadastro de mercados — espelha `MercadoCadastro` / `empresa.mercados`.
    `slug` é o mesmo id usado no frontend (gerado por `slugify(nome)` lá)."""

    __tablename__ = "mercados"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    slug = db.Column(db.String(120), nullable=False)
    nome = db.Column(db.String(200), nullable=False)
    endereco_rua = db.Column(db.String(300), default="")
    endereco_bairro = db.Column(db.String(150), default="")
    endereco_cidade = db.Column(db.String(150), default="")
    endereco_estado = db.Column(db.String(10), default="")
    atualizado_em = db.Column(db.DateTime(timezone=True), default=_agora, onupdate=_agora)

    empresa = db.relationship("Empresa", back_populates="mercados")
    saldos = db.relationship("SaldoEstoque", back_populates="mercado", lazy="dynamic")

    __table_args__ = (db.UniqueConstraint("empresa_id", "slug", name="uq_mercado_empresa_slug"),)


class SaldoEstoque(db.Model):
    """Espelha `estoquePorMercado[mercadoId][codigoEAN]` — é a tabela
    ESPARSA: só existe uma linha aqui quando alguém de fato contou/lançou
    aquele produto naquele mercado (mesma filosofia do frontend, que foi o
    que resolveu o bug de "produto não aparece pro mercado X")."""

    __tablename__ = "saldos_estoque"

    id = db.Column(db.Integer, primary_key=True)
    mercado_id = db.Column(db.Integer, db.ForeignKey("mercados.id"), nullable=False)
    produto_id = db.Column(db.Integer, db.ForeignKey("produtos.id"), nullable=False)
    quantidade_atual = db.Column(db.Integer, default=0)
    quantidade_minima = db.Column(db.Integer, default=0)
    preco_unitario = db.Column(db.Numeric(10, 2), nullable=True)  # R$ — mesmo campo opcional do frontend
    atualizado_em = db.Column(db.DateTime(timezone=True), default=_agora, onupdate=_agora)

    mercado = db.relationship("Mercado", back_populates="saldos")
    produto = db.relationship("Produto", back_populates="saldos")

    __table_args__ = (db.UniqueConstraint("mercado_id", "produto_id", name="uq_saldo_mercado_produto"),)


class Movimentacao(db.Model):
    """Ledger append-only — espelha `Movimentacao[]`. Guarda `codigo_ean` e
    `mercado_slug` direto (sem FK obrigatória) de propósito: numa fila
    offline, uma movimentação pode chegar ANTES do produto/mercado terem
    sido formalmente sincronizados — isso aqui nunca pode rejeitar um
    lançamento só por causa de ordem de chegada."""

    __tablename__ = "movimentacoes"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)  # mesmo uuid do cliente
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    mercado_slug = db.Column(db.String(120), nullable=False)
    codigo_ean = db.Column(db.String(32), nullable=False)
    tipo = db.Column(db.String(10), nullable=False)  # 'entrada' | 'saida'
    motivo = db.Column(db.String(20), nullable=False)  # 'venda' | 'avaria' | 'vencimento' | 'ajuste'
    quantidade = db.Column(db.Integer, nullable=False)
    operador = db.Column(db.String(200))
    data = db.Column(db.DateTime(timezone=True), default=_agora)
    ciclo_id = db.Column(db.String(20))  # ex: "2026-W41"


class PesquisaSemanal(db.Model):
    """Espelha `PesquisaSemanal` — o fechamento de uma contagem."""

    __tablename__ = "pesquisas_semanais"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    mercado_slug = db.Column(db.String(120), nullable=False)
    ciclo_id = db.Column(db.String(20), nullable=False)
    operador = db.Column(db.String(200))
    fechada_em = db.Column(db.DateTime(timezone=True), default=_agora)

    itens = db.relationship("PesquisaSemanalItem", back_populates="pesquisa", cascade="all, delete-orphan")


class PesquisaSemanalItem(db.Model):
    __tablename__ = "pesquisas_semanais_itens"

    id = db.Column(db.Integer, primary_key=True)
    pesquisa_id = db.Column(db.String(36), db.ForeignKey("pesquisas_semanais.id"), nullable=False)
    codigo_ean = db.Column(db.String(32), nullable=False)
    quantidade_esperada = db.Column(db.Integer, default=0)
    quantidade_contada = db.Column(db.Integer, default=0)
    divergencia = db.Column(db.Integer, default=0)
    # 'unidade' | 'caixa' (2026-10-13) — mesmo padrão de ProdutoRegistrado.quantity_type
    quantity_type = db.Column(db.String(20), nullable=False, default="unidade")

    pesquisa = db.relationship("PesquisaSemanal", back_populates="itens")


class ProdutoRegistrado(db.Model):
    """Espelha `Product` / `productStorage` — o fluxo de Registrar (item
    perecível com validade), independente do módulo de Estoque. É o dado
    mais antigo do app, existia antes do módulo de estoque."""

    __tablename__ = "produtos_registrados"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    operator_name = db.Column(db.String(200))
    ean_code = db.Column(db.String(32))
    description = db.Column(db.String(300))
    quantity = db.Column(db.Integer)
    quantity_type = db.Column(db.String(20))  # 'unidade' | 'caixa'
    expiration_date = db.Column(db.String(40))  # mantido como string ISO, igual ao frontend
    registration_date = db.Column(db.String(40))
    image_url = db.Column(db.Text, nullable=True)
    nome_cliente = db.Column(db.String(200))
    endereco_cliente = db.Column(db.String(300))
    # Soft delete — o cliente roda offline e pode excluir um item enquanto
    # outro dispositivo ainda não sincronizou o cadastro dele; apagar a linha
    # de verdade perderia a chance de reconciliar isso. GET /produtos-registrados
    # já filtra `deletado=False` por padrão, então na prática o app nunca vê
    # o item excluído — mas o histórico fica preservado no banco.
    deletado = db.Column(db.Boolean, default=False, nullable=False)
    atualizado_em = db.Column(db.DateTime(timezone=True), default=_agora, onupdate=_agora)


class Avaria(db.Model):
    """Registro de avaria/quebra — página dedicada no frontend (EAN + Setup
    Rápido, mesmo padrão de ProdutoRegistrado). Diferente de uma simples
    `Movimentacao` (que já tinha `motivo='avaria'` mas nunca teve nenhuma
    tela própria pra gerar esse motivo): aqui cada avaria é um registro
    completo (quem, o quê, quando, por quê), pensado pra virar relatório
    (.docx) de verdade. Registrar uma avaria TAMBÉM cria uma `Movimentacao`
    de saída com motivo='avaria' e abate o saldo — ver
    `handle_avaria_registrar` em app/sync/handlers.py — pra nunca existir
    uma avaria "fantasma" que não bateu com o estoque."""

    __tablename__ = "avarias"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)  # mesmo uuid do cliente
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    mercado_slug = db.Column(db.String(120), nullable=False)
    codigo_ean = db.Column(db.String(32), nullable=False)
    descricao = db.Column(db.String(300))
    quantidade = db.Column(db.Integer, nullable=False)
    # 'unidade' | 'caixa' (2026-10-13) — mesmo padrão de ProdutoRegistrado.quantity_type
    quantity_type = db.Column(db.String(20), nullable=False, default="unidade")
    motivo = db.Column(db.String(300))  # texto livre: "quebrou no transporte", "caixa amassada", etc. — opcional
    operador = db.Column(db.String(200))
    nome_cliente = db.Column(db.String(200))
    endereco_cliente = db.Column(db.String(300))
    registrado_em = db.Column(db.DateTime(timezone=True), default=_agora)


class LoginLog(db.Model):
    """Auditoria simples de acesso — uma linha por login bem-sucedido.
    Pedido do usuário ao perceber que não existia nenhum jeito de saber
    quem tinha usado o sistema e quando. `nome`/`perfil` são um SNAPSHOT do
    momento do login (não um JOIN em Usuario) — assim o histórico continua
    legível e correto mesmo que o usuário seja renomeado ou tenha o perfil
    trocado depois."""

    __tablename__ = "login_logs"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    nome = db.Column(db.String(200), nullable=False)
    perfil = db.Column(db.String(20), nullable=False)
    ip = db.Column(db.String(64), nullable=True)
    logado_em = db.Column(db.DateTime(timezone=True), default=_agora)


class FilaSyncLog(db.Model):
    """Espelho server-side da fila do cliente (client/src/lib/syncQueue.ts).
    A chave primária é O MESMO uuid que o item tem no IndexedDB do cliente —
    isso é o que torna o endpoint /api/sync idempotente: se o mesmo item
    chegar duas vezes (ex: operador saiu da câmara fria, o POST foi
    processado mas a resposta se perdeu antes de confirmar pro cliente, e o
    cliente tenta de novo), a segunda chamada só confirma de volta sem
    reprocessar — sem duplicar movimentação, sem duplicar pesquisa semanal."""

    __tablename__ = "fila_sync_log"

    id = db.Column(db.String(36), primary_key=True)  # = ItemFilaSync.id do cliente
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=True)
    tipo = db.Column(db.String(60), nullable=False)
    payload_json = db.Column(db.Text, nullable=False)
    recebido_em = db.Column(db.DateTime(timezone=True), default=_agora)
    processado = db.Column(db.Boolean, default=False)
    erro = db.Column(db.Text, nullable=True)
