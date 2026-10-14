# Port em Python de client/src/lib/estoqueAnalytics.ts — só a parte usada
# no relatório "Giro Registrado" (.docx), pra poder imprimir a "sugestão de
# pedido calculada" num documento gerado no servidor (pedido do usuário,
# PDF "Questionamento Técnico e Especificação da Nova Task", 2026-10-14,
# seção 3: "Dados Obrigatórios no Documento ... a sugestão de pedido
# calculada/recomendada").
#
# IMPORTANTE — leia antes de mexer aqui: os três parâmetros de configuração
# (lead time, dias até a próxima pesquisa, validade média PADRÃO) no
# frontend são editáveis pelo usuário na tela do Painel de Sugestão e ficam
# salvos em localStorage POR APARELHO (`CONFIGURACAO_PADRAO` /
# `salvarConfiguracao`/`carregarConfiguracao` em estoqueStorage.ts) — NUNCA
# sincronizados pro backend. Esta cópia em Python usa sempre os valores de
# fábrica (lead time 2 dias, 7 dias até a próxima pesquisa). Se o operador
# tiver alterado essa configuração no aparelho dele, o número impresso
# neste relatório pode divergir um pouco do número mostrado na tela do
# Painel de Sugestão naquele aparelho específico. A validade média
# (`validadeMediaDias`) já vem do catálogo (`Produto.validade_media_dias`),
# esse sim sincronizado — não sofre desse problema.
#
# As fórmulas abaixo espelham, nessa ordem, `calcularTaxaDiaria`,
# `calcularSugestao` e `calcularSugestaoCompleta` em estoqueAnalytics.ts.
# Qualquer mudança de regra de negócio ali precisa ser replicada aqui
# manualmente — não há import compartilhado entre TS e Python.

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, TypedDict

LEAD_TIME_DIAS_PADRAO = 2
DIAS_ATE_PESQUISA_PADRAO = 7
VALIDADE_MEDIA_DIAS_PADRAO = 15
DIAS_JANELA_PADRAO = 14


class ResultadoSugestao(TypedDict):
    taxaDiaria: float
    diasDeCobertura: Optional[float]
    sugestaoPedido: int
    urgencia: str
    tendencia: str


def _taxa_diaria(movimentacoes: list, dias_janela: int, dias_offset: int = 0) -> float:
    """Saídas motivo=venda OU motivo=ajuste na janela — mesma regra (e
    mesmo comentário histórico) de `calcularTaxaDiaria` no TS: "ajuste" conta
    porque é hoje o único jeito real de medir consumo (divergência negativa
    da Pesquisa Semanal).

    `m.data` vem do SQLite como datetime NAIVE mesmo a coluna sendo
    `DateTime(timezone=True)` — o SQLite não guarda de fato o tzinfo, só o
    valor (que é sempre UTC, gravado por `_agora()` em models.py). Por isso
    os limites de janela aqui também precisam ser naive, senão a
    comparação `inicio <= m.data < fim` explode com
    "can't compare offset-naive and offset-aware datetimes"."""
    if dias_janela <= 0:
        return 0.0
    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    fim = agora - timedelta(days=dias_offset)
    inicio = fim - timedelta(days=dias_janela)
    total = sum(
        m.quantidade
        for m in movimentacoes
        if m.tipo == "saida" and m.motivo in ("venda", "ajuste") and m.data is not None and inicio <= m.data < fim
    )
    return total / dias_janela


def calcular_sugestao_completa(
    movimentacoes: Iterable,
    *,
    quantidade_atual: int,
    validade_media_dias: Optional[int],
    dias_janela: int = DIAS_JANELA_PADRAO,
    lead_time_dias: int = LEAD_TIME_DIAS_PADRAO,
    dias_ate_proxima_pesquisa: int = DIAS_ATE_PESQUISA_PADRAO,
) -> ResultadoSugestao:
    """Equivalente a `calcularSugestaoCompleta` (que por sua vez chama
    `calcularTaxaDiaria` duas vezes — janela atual e anterior — e
    `calcularSugestao`). `movimentacoes` já deve vir filtrada por
    mercado+EAN (o filtro de mercado/produto é responsabilidade de quem
    chama, igual no TS é responsabilidade de quem monta a lista)."""
    validade_media_dias = validade_media_dias or VALIDADE_MEDIA_DIAS_PADRAO
    movs = list(movimentacoes)

    taxa_diaria = _taxa_diaria(movs, dias_janela)
    taxa_diaria_semana_anterior = _taxa_diaria(movs, dias_janela, dias_offset=dias_janela)

    if taxa_diaria <= 0:
        # sem saída registrada ainda — não há base estatística pra sugerir nada
        return {"taxaDiaria": 0.0, "diasDeCobertura": None, "sugestaoPedido": 0, "urgencia": "ok", "tendencia": "estavel"}

    estoque_seguranca = taxa_diaria * lead_time_dias
    ponto_de_pedido = estoque_seguranca + taxa_diaria * dias_ate_proxima_pesquisa
    sugestao_pedido = max(0.0, ponto_de_pedido - quantidade_atual)

    teto_por_validade = max(0.0, taxa_diaria * validade_media_dias - quantidade_atual)
    sugestao_pedido = round(min(sugestao_pedido, teto_por_validade))

    dias_de_cobertura = quantidade_atual / taxa_diaria

    if dias_de_cobertura < lead_time_dias:
        urgencia = "critico"
    elif dias_de_cobertura < lead_time_dias + 3:
        urgencia = "atencao"
    else:
        urgencia = "ok"

    variacao = (
        (taxa_diaria - taxa_diaria_semana_anterior) / taxa_diaria_semana_anterior
        if taxa_diaria_semana_anterior > 0
        else 0.0
    )
    tendencia = "subindo" if variacao > 0.15 else "caindo" if variacao < -0.15 else "estavel"

    return {
        "taxaDiaria": taxa_diaria,
        "diasDeCobertura": dias_de_cobertura,
        "sugestaoPedido": sugestao_pedido,
        "urgencia": urgencia,
        "tendencia": tendencia,
    }
