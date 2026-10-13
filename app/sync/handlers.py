# Um handler por `tipo` de ação, no MESMO formato que o cliente já produz
# (ver os 5 pontos em estoqueStorage.ts + 1 em localStorage.ts que chamam
# `enfileirar(tipo, payload)`). Isso é o que permite ao endpoint /api/sync
# ser genérico: recebe {tipo, payload}, despacha pro handler certo, pronto.
#
# Toda função aqui é "get-or-create" nas entidades que referencia (mercado,
# produto) — numa fila offline, os eventos podem chegar fora de ordem (ex:
# uma movimentação de um mercado que só foi criado localmente, nunca
# sincronizado via 'estoque.cadastrarProduto' formalmente). Rejeitar por
# causa de ordem de chegada seria perder dado real do operador — o jeito
# certo é sempre materializar o que falta com os dados que vieram junto.

from datetime import datetime

from app.extensions import db
from app.models import Empresa, Produto, Mercado, SaldoEstoque, Movimentacao, PesquisaSemanal, PesquisaSemanalItem, ProdutoRegistrado, Avaria


def _get_empresa(empresa_slug: str) -> Empresa:
    empresa = Empresa.query.filter_by(slug=empresa_slug).first()
    if not empresa:
        empresa = Empresa(slug=empresa_slug, nome=empresa_slug.capitalize())
        db.session.add(empresa)
        db.session.flush()  # garante empresa.id sem precisar commitar ainda
    return empresa


def _get_or_create_mercado(
    empresa: Empresa, slug: str, nome: str | None = None, endereco: dict | None = None
) -> Mercado:
    """Get-or-create de sempre (ordem de chegada da fila offline — ver
    comentário no topo do arquivo), mas com uma correção (2026-10-13): a
    maioria dos eventos de sync (movimentação, pesquisa semanal, avaria...)
    só manda o `slug` do mercado, nunca o nome de verdade — se fossem eles a
    criar o registro do zero, `nome` cairia pro próprio slug (ex.:
    "demonstracao-sugestao" em vez de "Demonstração Sugestão"), e o
    pull-sync levaria esse nome degradado de volta pro cliente. Por isso,
    quando um `nome` de verdade chega aqui (só o evento dedicado
    `estoque.garantirMercado` manda isso — ver handle_estoque_garantir_mercado
    abaixo), a gente ATUALIZA o registro mesmo se ele já existir, o que
    também autocura um mercado que já tinha caído pro slug antes dessa
    correção existir."""
    mercado = Mercado.query.filter_by(empresa_id=empresa.id, slug=slug).first()
    if not mercado:
        mercado = Mercado(empresa_id=empresa.id, slug=slug, nome=nome or slug)
        if endereco:
            mercado.endereco_rua = endereco.get("rua", "")
            mercado.endereco_bairro = endereco.get("bairro", "")
            mercado.endereco_cidade = endereco.get("cidade", "")
            mercado.endereco_estado = endereco.get("estado", "")
        db.session.add(mercado)
        db.session.flush()
    elif nome and mercado.nome != nome:
        mercado.nome = nome
        if endereco:
            mercado.endereco_rua = endereco.get("rua", "")
            mercado.endereco_bairro = endereco.get("bairro", "")
            mercado.endereco_cidade = endereco.get("cidade", "")
            mercado.endereco_estado = endereco.get("estado", "")
    return mercado


def _get_or_create_produto(empresa: Empresa, codigo_ean: str, descricao: str | None = None, **extra) -> Produto:
    produto = Produto.query.filter_by(empresa_id=empresa.id, codigo_ean=codigo_ean).first()
    if not produto:
        produto = Produto(empresa_id=empresa.id, codigo_ean=codigo_ean, descricao=descricao or codigo_ean, **extra)
        db.session.add(produto)
        db.session.flush()
    elif descricao and produto.descricao != descricao:
        produto.descricao = descricao  # catálogo sincroniza a descrição mais recente, igual o seedEmpresaFrutap do frontend
    return produto


def _get_or_create_saldo(mercado: Mercado, produto: Produto) -> SaldoEstoque:
    saldo = SaldoEstoque.query.filter_by(mercado_id=mercado.id, produto_id=produto.id).first()
    if not saldo:
        saldo = SaldoEstoque(mercado_id=mercado.id, produto_id=produto.id, quantidade_atual=0, quantidade_minima=0)
        db.session.add(saldo)
        db.session.flush()
    return saldo


def _parse_data(valor: str | None) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Handlers — um por TipoAcaoSync (ver client/src/lib/syncQueue.ts)
# ---------------------------------------------------------------------------


def handle_produto_registrar(payload: dict) -> None:
    empresa = _get_empresa(payload.get("empresaId", "frutap"))
    existente = ProdutoRegistrado.query.get(payload["id"])
    dados = dict(
        empresa_id=empresa.id,
        operator_name=payload.get("operatorName"),
        ean_code=payload.get("eanCode"),
        description=payload.get("description"),
        quantity=payload.get("quantity"),
        quantity_type=payload.get("quantityType"),
        expiration_date=payload.get("expirationDate"),
        registration_date=payload.get("registrationDate"),
        image_url=payload.get("imageUrl"),
        nome_cliente=payload.get("nomeCliente"),
        endereco_cliente=payload.get("enderecoCliente"),
    )
    if existente:
        for campo, valor in dados.items():
            setattr(existente, campo, valor)
    else:
        db.session.add(ProdutoRegistrado(id=payload["id"], **dados))


def handle_produto_deletar(payload: dict) -> None:
    """Soft delete — ver o comentário em `ProdutoRegistrado.deletado` no
    models.py pro porquê de não apagar a linha de verdade."""
    produto = ProdutoRegistrado.query.get(payload["id"])
    if produto:
        produto.deletado = False if payload.get("restaurar") else True


def handle_avaria_registrar(payload: dict) -> None:
    """Registra a avaria E abate o saldo na mesma transação — uma avaria
    nunca fica "fantasma" sem bater com o estoque, e o estoque nunca cai
    sem deixar rastro de por quê. Idempotente por natureza (o `id` do
    envelope de sync em FilaSyncLog é quem evita reprocessar — ver
    app/sync/routes.py), então não precisa de nenhuma checagem extra aqui."""
    empresa = _get_empresa(payload.get("empresaId", "frutap"))
    mercado = _get_or_create_mercado(empresa, payload["mercadoId"])
    produto = _get_or_create_produto(empresa, payload["codigoEAN"], descricao=payload.get("descricao"))
    saldo = _get_or_create_saldo(mercado, produto)

    quantidade = payload["quantidade"]
    saldo.quantidade_atual = max(0, saldo.quantidade_atual - quantidade)

    db.session.add(
        Avaria(
            id=payload["id"],
            empresa_id=empresa.id,
            mercado_slug=mercado.slug,
            codigo_ean=produto.codigo_ean,
            descricao=payload.get("descricao") or produto.descricao,
            quantidade=quantidade,
            motivo=payload.get("motivo"),
            operador=payload.get("operador"),
            nome_cliente=payload.get("nomeCliente"),
            endereco_cliente=payload.get("enderecoCliente"),
        )
    )

    db.session.add(
        Movimentacao(
            empresa_id=empresa.id,
            mercado_slug=mercado.slug,
            codigo_ean=produto.codigo_ean,
            tipo="saida",
            motivo="avaria",
            quantidade=quantidade,
            operador=payload.get("operador"),
            ciclo_id=payload.get("cicloId"),
        )
    )


def handle_estoque_garantir_mercado(payload: dict) -> None:
    """Evento dedicado (2026-10-13) pra levar o NOME DE VERDADE do mercado
    pro backend — ver `garantirMercado` em client/src/lib/estoqueStorage.ts,
    que enfileira isso toda vez que resolve/cria um mercado localmente (Setup
    Rápido, Pesquisa Semanal, Avarias). Sem esse evento, nenhum outro ponto
    da fila de sync carrega o nome (só o slug), e o backend acabava criando
    o mercado com `nome = slug` assim que o primeiro evento relacionado a
    ele chegasse fora de ordem."""
    empresa = _get_empresa(payload.get("empresaId", "frutap"))
    _get_or_create_mercado(
        empresa,
        payload["mercadoId"],
        nome=payload.get("nome"),
        endereco=payload.get("endereco"),
    )


def handle_estoque_cadastrar_produto(payload: dict) -> None:
    empresa = _get_empresa(payload.get("empresaId", "frutap"))
    produto = payload.get("produto", {})
    _get_or_create_produto(
        empresa,
        payload["codigoEAN"],
        descricao=produto.get("descricao"),
        marca=produto.get("marca", ""),
        categoria=produto.get("categoria", ""),
        unidade=produto.get("unidade", "unidade"),
        validade_media_dias=produto.get("validadeMediaDias", 15),
    )


def handle_estoque_definir_estoque_inicial(payload: dict) -> None:
    empresa = _get_empresa(payload.get("empresaId", "frutap"))
    mercado = _get_or_create_mercado(empresa, payload["mercadoId"])
    produto = _get_or_create_produto(empresa, payload["codigoEAN"])
    saldo = _get_or_create_saldo(mercado, produto)
    saldo.quantidade_atual = payload.get("quantidadeInicial", 0)


def handle_estoque_definir_preco(payload: dict) -> None:
    empresa = _get_empresa(payload.get("empresaId", "frutap"))
    mercado = _get_or_create_mercado(empresa, payload["mercadoId"])
    produto = _get_or_create_produto(empresa, payload["codigoEAN"])
    saldo = _get_or_create_saldo(mercado, produto)
    saldo.preco_unitario = payload.get("precoUnitario")


def handle_estoque_registrar_movimentacao(payload: dict) -> None:
    empresa = _get_empresa(payload.get("empresaId", "frutap"))
    mercado = _get_or_create_mercado(empresa, payload["mercadoId"])
    produto = _get_or_create_produto(empresa, payload["codigoEAN"])
    saldo = _get_or_create_saldo(mercado, produto)

    delta = payload["quantidade"] if payload["tipo"] == "entrada" else -payload["quantidade"]
    saldo.quantidade_atual = max(0, saldo.quantidade_atual + delta)

    db.session.add(
        Movimentacao(
            empresa_id=empresa.id,
            mercado_slug=mercado.slug,
            codigo_ean=produto.codigo_ean,
            tipo=payload["tipo"],
            motivo=payload["motivo"],
            quantidade=payload["quantidade"],
            operador=payload.get("operador"),
            ciclo_id=payload.get("cicloId"),
        )
    )


def handle_estoque_fechar_pesquisa_semanal(payload: dict) -> None:
    empresa = _get_empresa(payload.get("empresaId", "frutap"))
    mercado = _get_or_create_mercado(empresa, payload["mercadoId"])

    pesquisa = PesquisaSemanal.query.get(payload["id"])
    if pesquisa:
        return  # já processada (idempotência por id, igual o FilaSyncLog)

    pesquisa = PesquisaSemanal(
        id=payload["id"],
        empresa_id=empresa.id,
        mercado_slug=mercado.slug,
        ciclo_id=payload["cicloId"],
        operador=payload.get("operador"),
    )
    db.session.add(pesquisa)

    for item in payload.get("itens", []):
        produto = _get_or_create_produto(empresa, item["codigoEAN"])
        saldo = _get_or_create_saldo(mercado, produto)
        saldo.quantidade_atual = item["quantidadeContada"]

        db.session.add(
            PesquisaSemanalItem(
                pesquisa_id=pesquisa.id,
                codigo_ean=item["codigoEAN"],
                quantidade_esperada=item["quantidadeEsperada"],
                quantidade_contada=item["quantidadeContada"],
                divergencia=item["divergencia"],
            )
        )


HANDLERS = {
    "produto.registrar": handle_produto_registrar,
    # 'atualizar' é literalmente o mesmo upsert-por-id que 'registrar' já
    # fazia (handle_produto_registrar já trata "existe -> atualiza campos,
    # não existe -> cria") — reaproveita a função em vez de duplicar lógica.
    "produto.atualizar": handle_produto_registrar,
    "produto.deletar": handle_produto_deletar,
    "avaria.registrar": handle_avaria_registrar,
    "estoque.cadastrarProduto": handle_estoque_cadastrar_produto,
    "estoque.garantirMercado": handle_estoque_garantir_mercado,
    "estoque.definirEstoqueInicial": handle_estoque_definir_estoque_inicial,
    "estoque.definirPreco": handle_estoque_definir_preco,
    "estoque.registrarMovimentacao": handle_estoque_registrar_movimentacao,
    "estoque.fecharPesquisaSemanal": handle_estoque_fechar_pesquisa_semanal,
}
