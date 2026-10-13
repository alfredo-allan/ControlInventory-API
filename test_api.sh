#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# test_api.sh — bateria de testes end-to-end da API do ControlInventory.
#
# Objetivo: confirmar, contra um servidor Flask rodando de verdade, que:
#   1) os 6 tipos de ação de sincronização aceitam exatamente o payload que
#      o frontend (syncQueue.ts) já produz, sem precisar mudar nada no código
#      do cliente quando formos ligar o fetch() real;
#   2) a idempotência funciona (reenviar o mesmo item da fila não duplica
#      nada no banco — essencial pro cenário de operador saindo da câmara
#      fria e reenviando depois de perder a resposta);
#   3) o RBAC (admin x operador) bloqueia corretamente quem não tem permissão;
#   4) os endpoints de leitura devolvem os dados no formato esperado.
#
# Cada rodada usa um mercado/produto com sufixo de timestamp, então rodar
# este script várias vezes seguidas NUNCA conflita com dados de uma rodada
# anterior nem falsifica as contagens.
#
# Uso:
#   chmod +x test_api.sh
#   ./test_api.sh                                   # usa os padrões abaixo
#   BASE_URL=http://127.0.0.1:5050 ./test_api.sh
#   ADMIN_SENHA="sua-senha-do-seed" ./test_api.sh
#   TESTAR_LIMPAR_LOGS=true ./test_api.sh           # exercita de verdade o
#                                                    # hard-delete de POST
#                                                    # /api/admin/logs/limpar
#                                                    # (DESTRUTIVO — só contra
#                                                    # um banco descartável;
#                                                    # por padrão esse passo
#                                                    # só valida RBAC/400)
#
# Pré-requisitos: curl e jq instalados (`sudo apt install -y curl jq`),
# e a API já rodando (python -m flask run --port 5050) com o seed.py
# já executado pelo menos uma vez (precisa existir o usuário "admin").
# ---------------------------------------------------------------------------

set -u

BASE_URL="${BASE_URL:-http://127.0.0.1:5050}"
ADMIN_NOME="${ADMIN_NOME:-admin}"
ADMIN_SENHA="${ADMIN_SENHA:-troque-essa-senha}"

TS=$(date +%s)
MERCADO_SLUG="mercado-teste-sync-${TS}"
EAN_NOVO="9${TS}"
OPERADOR_NOME="operador-teste-${TS}"

OK=0
FALHAS=0
TOKEN_ADMIN=""
TOKEN_OPERADOR=""

VERDE='\033[0;32m'
VERMELHO='\033[0;31m'
AMARELO='\033[1;33m'
AZUL='\033[0;34m'
SEM_COR='\033[0m'

# ---------------------------------------------------------------------------
# Checagem de dependências
# ---------------------------------------------------------------------------

for bin in curl jq; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo -e "${VERMELHO}Falta o comando '$bin'. Instale com: sudo apt install -y curl jq${SEM_COR}"
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Faz a chamada HTTP e deixa em $BODY e $STATUS o resultado.
chamar() {
  local metodo="$1" caminho="$2" dados="${3:-}" token="${4:-}"
  local resposta
  local -a args=(-s -w '\n%{http_code}' -X "$metodo" "${BASE_URL}${caminho}" -H "Content-Type: application/json")
  [ -n "$token" ] && args+=(-H "Authorization: Bearer ${token}")
  [ -n "$dados" ] && args+=(-d "$dados")
  resposta=$(curl "${args[@]}")
  STATUS=$(echo "$resposta" | tail -n1)
  BODY=$(echo "$resposta" | sed '$d')
}

secao() {
  echo
  echo -e "${AZUL}── $1 ──${SEM_COR}"
}

checar() {
  # checar "descrição" status_esperado status_obtido [jq_filtro jq_esperado]
  local descricao="$1" esperado="$2" obtido="$3"
  if [ "$obtido" = "$esperado" ]; then
    echo -e "  ${VERDE}✔${SEM_COR} ${descricao} (HTTP ${obtido})"
    OK=$((OK + 1))
    return 0
  else
    echo -e "  ${VERMELHO}✘${SEM_COR} ${descricao} — esperado HTTP ${esperado}, veio ${obtido}"
    echo -e "    ${AMARELO}resposta:${SEM_COR} ${BODY}"
    FALHAS=$((FALHAS + 1))
    return 1
  fi
}

checar_valor() {
  # checar_valor "descrição" "$BODY" '.filtro.jq' "valor_esperado"
  local descricao="$1" corpo="$2" filtro="$3" esperado="$4"
  local obtido
  obtido=$(echo "$corpo" | jq -r "$filtro" 2>/dev/null)
  if [ "$obtido" = "$esperado" ]; then
    echo -e "  ${VERDE}✔${SEM_COR} ${descricao} (${filtro} = ${obtido})"
    OK=$((OK + 1))
  else
    echo -e "  ${VERMELHO}✘${SEM_COR} ${descricao} — esperado '${esperado}', veio '${obtido}' (${filtro})"
    FALHAS=$((FALHAS + 1))
  fi
}

# ---------------------------------------------------------------------------
# 0. API no ar?
# ---------------------------------------------------------------------------

secao "0. Saúde da API"
chamar GET "/api/health"
checar "GET /api/health responde" "200" "$STATUS"

# ---------------------------------------------------------------------------
# 1. Login do admin + RBAC básico (sem token)
# ---------------------------------------------------------------------------

secao "1. Autenticação"
chamar POST "/api/auth/login" "{\"nome\":\"${ADMIN_NOME}\",\"senha\":\"${ADMIN_SENHA}\"}"
checar "login do admin" "200" "$STATUS"
TOKEN_ADMIN=$(echo "$BODY" | jq -r '.token // empty')
if [ -z "$TOKEN_ADMIN" ]; then
  echo -e "${VERMELHO}Não consegui logar como '${ADMIN_NOME}'. Confira ADMIN_SENHA (padrão do seed.py é 'troque-essa-senha', a não ser que você tenha passado --admin-senha).${SEM_COR}"
  echo -e "${AMARELO}resposta do login:${SEM_COR} ${BODY}"
  exit 1
fi

chamar GET "/api/auth/me" "" "$TOKEN_ADMIN"
checar "GET /api/auth/me com token válido" "200" "$STATUS"

chamar GET "/api/produtos"
checar "GET /api/produtos sem token é bloqueado" "401" "$STATUS"

# ---------------------------------------------------------------------------
# 2. Catálogo (deve ter o seed: 82 produtos / 14 mercados, ou mais se você
#    já usou o app depois do seed — por isso checamos só "maior que zero")
# ---------------------------------------------------------------------------

secao "2. Catálogo (leitura)"
chamar GET "/api/produtos" "" "$TOKEN_ADMIN"
checar "GET /api/produtos com token" "200" "$STATUS"
QTD_PRODUTOS=$(echo "$BODY" | jq 'length')
if [ "${QTD_PRODUTOS:-0}" -gt 0 ]; then
  echo -e "  ${VERDE}✔${SEM_COR} catálogo tem produtos (${QTD_PRODUTOS})"
  OK=$((OK + 1))
else
  echo -e "  ${VERMELHO}✘${SEM_COR} catálogo veio vazio — rodou o seed.py?"
  FALHAS=$((FALHAS + 1))
fi

chamar GET "/api/mercados" "" "$TOKEN_ADMIN"
checar "GET /api/mercados com token" "200" "$STATUS"

# ---------------------------------------------------------------------------
# 3. Os 6 tipos de ação de sincronização — payloads no MESMO formato que
#    client/src/lib/estoqueStorage.ts e localStorage.ts já montam hoje.
# ---------------------------------------------------------------------------

secao "3. Fila de sincronização — os 6 tipos de ação"

ID_PRODUTO_REG=$(cat /proc/sys/kernel/random/uuid 2>/dev/null || python3 -c "import uuid;print(uuid.uuid4())")
chamar POST "/api/sync" "{
  \"id\": \"${ID_PRODUTO_REG}\", \"tipo\": \"produto.registrar\",
  \"payload\": {
    \"id\": \"${ID_PRODUTO_REG}\", \"operatorName\": \"Operador Teste\", \"eanCode\": \"${EAN_NOVO}\",
    \"description\": \"Produto Teste Sync\", \"quantity\": 10, \"quantityType\": \"unidade\",
    \"expirationDate\": \"2026-12-01\", \"registrationDate\": \"2026-10-13\",
    \"nomeCliente\": \"Cliente X\", \"enderecoCliente\": \"Rua Teste, 123\"
  }
}" "$TOKEN_ADMIN"
checar "produto.registrar" "200" "$STATUS"

ID_CADASTRAR=$(python3 -c "import uuid;print(uuid.uuid4())")
chamar POST "/api/sync" "{
  \"id\": \"${ID_CADASTRAR}\", \"tipo\": \"estoque.cadastrarProduto\",
  \"payload\": {
    \"empresaId\": \"frutap\", \"codigoEAN\": \"${EAN_NOVO}\",
    \"produto\": {\"descricao\": \"Produto Teste Sync\", \"marca\": \"MarcaX\", \"categoria\": \"Categoria Y\",
                  \"unidade\": \"unidade\", \"validadeMediaDias\": 20}
  }
}" "$TOKEN_ADMIN"
checar "estoque.cadastrarProduto" "200" "$STATUS"

ID_INICIAL=$(python3 -c "import uuid;print(uuid.uuid4())")
chamar POST "/api/sync" "{
  \"id\": \"${ID_INICIAL}\", \"tipo\": \"estoque.definirEstoqueInicial\",
  \"payload\": {
    \"empresaId\": \"frutap\", \"mercadoId\": \"${MERCADO_SLUG}\", \"codigoEAN\": \"${EAN_NOVO}\",
    \"quantidadeInicial\": 100, \"operador\": \"Operador Teste\"
  }
}" "$TOKEN_ADMIN"
checar "estoque.definirEstoqueInicial" "200" "$STATUS"

ID_PRECO=$(python3 -c "import uuid;print(uuid.uuid4())")
chamar POST "/api/sync" "{
  \"id\": \"${ID_PRECO}\", \"tipo\": \"estoque.definirPreco\",
  \"payload\": {
    \"empresaId\": \"frutap\", \"mercadoId\": \"${MERCADO_SLUG}\", \"codigoEAN\": \"${EAN_NOVO}\", \"precoUnitario\": 9.90
  }
}" "$TOKEN_ADMIN"
checar "estoque.definirPreco" "200" "$STATUS"

ID_MOVIMENTACAO=$(python3 -c "import uuid;print(uuid.uuid4())")
PAYLOAD_MOVIMENTACAO="{
    \"empresaId\": \"frutap\", \"mercadoId\": \"${MERCADO_SLUG}\", \"codigoEAN\": \"${EAN_NOVO}\",
    \"tipo\": \"saida\", \"motivo\": \"avaria\", \"quantidade\": 4, \"operador\": \"Operador Teste\", \"cicloId\": \"2026-W41\"
  }"
chamar POST "/api/sync" "{\"id\": \"${ID_MOVIMENTACAO}\", \"tipo\": \"estoque.registrarMovimentacao\", \"payload\": ${PAYLOAD_MOVIMENTACAO}}" "$TOKEN_ADMIN"
checar "estoque.registrarMovimentacao" "200" "$STATUS"

ID_PESQUISA=$(python3 -c "import uuid;print(uuid.uuid4())")
PAYLOAD_PESQUISA="{
    \"empresaId\": \"frutap\", \"id\": \"${ID_PESQUISA}\", \"mercadoId\": \"${MERCADO_SLUG}\", \"cicloId\": \"2026-W41\",
    \"operador\": \"Operador Teste\", \"fechadaEm\": \"2026-10-13T12:00:00Z\",
    \"itens\": [{\"codigoEAN\": \"${EAN_NOVO}\", \"quantidadeEsperada\": 100, \"quantidadeContada\": 96, \"divergencia\": -4}]
  }"
chamar POST "/api/sync" "{\"id\": \"${ID_PESQUISA}\", \"tipo\": \"estoque.fecharPesquisaSemanal\", \"payload\": ${PAYLOAD_PESQUISA}}" "$TOKEN_ADMIN"
checar "estoque.fecharPesquisaSemanal" "200" "$STATUS"

# ---------------------------------------------------------------------------
# 4. Produtos registrados (Farejar/Listar) — listar/atualizar/excluir.
#    Endpoint e tipos de sync adicionados em 2026-10-13 pra fechar a lacuna
#    de persistência que o Farejar tinha (ver claude/arquitetura-modulo-estoque.md).
# ---------------------------------------------------------------------------

secao "4. Produtos registrados (Farejar/Listar) — listar/atualizar/excluir"

chamar GET "/api/produtos-registrados" "" "$TOKEN_ADMIN"
checar "GET /api/produtos-registrados com token" "200" "$STATUS"

# ID_PRODUTO_REG foi registrado na seção 3 (produto.registrar) — confirma que
# ele aparece na listagem, com os dados que vieram no cadastro.
ITEM_REGISTRADO=$(echo "$BODY" | jq -c ".[] | select(.id == \"${ID_PRODUTO_REG}\")")
if [ -n "$ITEM_REGISTRADO" ]; then
  checar_valor "produto registrado aparece na listagem" "$ITEM_REGISTRADO" '.description' "Produto Teste Sync"
else
  echo -e "  ${VERMELHO}✘${SEM_COR} produto registrado na seção 3 não apareceu em /api/produtos-registrados"
  FALHAS=$((FALHAS + 1))
fi

# Importante: o id da ENVELOPE de sync (campo "id" de fora) precisa ser um
# UUID NOVO, diferente de ID_PRODUTO_REG — esse é o id do item NA FILA
# (FilaSyncLog, chave da idempotência), não o id da entidade sendo editada
# (que vai dentro de "payload"). É exatamente assim que syncQueue.ts já
# gera: um crypto.randomUUID() novo por AÇÃO enfileirada, mesmo quando duas
# ações mexem na mesma entidade. Reusar ID_PRODUTO_REG aqui faria o check de
# idempotência (idêntico ao da seção 5) achar que essa ação já tinha sido
# processada — e pular o handler, silenciosamente não aplicando a edição.
ID_ATUALIZAR=$(python3 -c "import uuid;print(uuid.uuid4())")
chamar POST "/api/sync" "{
  \"id\": \"${ID_ATUALIZAR}\", \"tipo\": \"produto.atualizar\",
  \"payload\": {
    \"id\": \"${ID_PRODUTO_REG}\", \"operatorName\": \"Operador Teste\", \"eanCode\": \"${EAN_NOVO}\",
    \"description\": \"Produto Teste Sync EDITADO\", \"quantity\": 7, \"quantityType\": \"unidade\",
    \"expirationDate\": \"2026-12-01\", \"registrationDate\": \"2026-10-13\",
    \"nomeCliente\": \"Cliente X\", \"enderecoCliente\": \"Rua Teste, 123\"
  }
}" "$TOKEN_ADMIN"
checar "produto.atualizar" "200" "$STATUS"

chamar GET "/api/produtos-registrados" "" "$TOKEN_ADMIN"
ITEM_ATUALIZADO=$(echo "$BODY" | jq -c ".[] | select(.id == \"${ID_PRODUTO_REG}\")")
checar_valor "edição refletida na listagem (descrição)" "$ITEM_ATUALIZADO" '.description' "Produto Teste Sync EDITADO"
checar_valor "edição refletida na listagem (quantidade)" "$ITEM_ATUALIZADO" '.quantity' "7"

chamar POST "/api/sync" "{\"id\": \"$(python3 -c 'import uuid;print(uuid.uuid4())')\", \"tipo\": \"produto.deletar\", \"payload\": {\"id\": \"${ID_PRODUTO_REG}\"}}" "$TOKEN_ADMIN"
checar "produto.deletar" "200" "$STATUS"

chamar GET "/api/produtos-registrados" "" "$TOKEN_ADMIN"
ITEM_APOS_DELETAR=$(echo "$BODY" | jq -c ".[] | select(.id == \"${ID_PRODUTO_REG}\")")
if [ -z "$ITEM_APOS_DELETAR" ]; then
  echo -e "  ${VERDE}✔${SEM_COR} produto excluído não aparece mais na listagem (soft delete)"
  OK=$((OK + 1))
else
  echo -e "  ${VERMELHO}✘${SEM_COR} produto excluído AINDA aparece na listagem"
  FALHAS=$((FALHAS + 1))
fi

# ---------------------------------------------------------------------------
# 5. Idempotência — reenviar o MESMO id não pode duplicar nada
# ---------------------------------------------------------------------------

secao "5. Idempotência (reenvio do mesmo item da fila)"

chamar POST "/api/sync" "{\"id\": \"${ID_MOVIMENTACAO}\", \"tipo\": \"estoque.registrarMovimentacao\", \"payload\": ${PAYLOAD_MOVIMENTACAO}}" "$TOKEN_ADMIN"
checar "reenvio da movimentação responde 200" "200" "$STATUS"
checar_valor "reenvio não reprocessa (status = ja_processado)" "$BODY" '.status' "ja_processado"

chamar POST "/api/sync" "{\"id\": \"${ID_PESQUISA}\", \"tipo\": \"estoque.fecharPesquisaSemanal\", \"payload\": ${PAYLOAD_PESQUISA}}" "$TOKEN_ADMIN"
checar "reenvio da pesquisa semanal responde 200" "200" "$STATUS"
checar_valor "reenvio não reprocessa (status = ja_processado)" "$BODY" '.status' "ja_processado"

# ---------------------------------------------------------------------------
# 6. Casos de erro esperados
# ---------------------------------------------------------------------------

secao "6. Casos de erro"

chamar POST "/api/sync" "{\"id\": \"$(python3 -c 'import uuid;print(uuid.uuid4())')\", \"tipo\": \"tipo.inexistente\", \"payload\": {}}" "$TOKEN_ADMIN"
checar "tipo de ação desconhecido retorna 400" "400" "$STATUS"

chamar POST "/api/sync" "{\"id\": \"$(python3 -c 'import uuid;print(uuid.uuid4())')\"}" "$TOKEN_ADMIN"
checar "payload incompleto retorna 400" "400" "$STATUS"

# ---------------------------------------------------------------------------
# 7. Verificação final — os dados realmente ficaram do jeito esperado
# ---------------------------------------------------------------------------

secao "7. Estado final no banco"

chamar GET "/api/estoque/${MERCADO_SLUG}" "" "$TOKEN_ADMIN"
checar "GET /api/estoque/<mercado>" "200" "$STATUS"
ITEM=$(echo "$BODY" | jq -c ".[] | select(.codigoEAN == \"${EAN_NOVO}\")")
if [ -n "$ITEM" ]; then
  checar_valor "descrição veio do cadastro (estoque.cadastrarProduto)" "$ITEM" '.produto.descricao' "Produto Teste Sync"
  checar_valor "quantidade final = 100 - 4 = 96" "$ITEM" '.produto.estoque.quantidadeAtual' "96"
  checar_valor "preço unitário = 9.9" "$ITEM" '.produto.estoque.precoUnitario' "9.9"
else
  echo -e "  ${VERMELHO}✘${SEM_COR} item de teste não apareceu em /api/estoque/${MERCADO_SLUG}"
  FALHAS=$((FALHAS + 1))
fi

chamar GET "/api/movimentacoes?mercado=${MERCADO_SLUG}" "" "$TOKEN_ADMIN"
checar "GET /api/movimentacoes" "200" "$STATUS"
checar_valor "só 1 movimentação (reenvio não duplicou)" "$BODY" 'length' "1"

chamar GET "/api/pesquisas-semanais?mercado=${MERCADO_SLUG}" "" "$TOKEN_ADMIN"
checar "GET /api/pesquisas-semanais" "200" "$STATUS"
checar_valor "só 1 pesquisa semanal (reenvio não duplicou)" "$BODY" 'length' "1"

chamar GET "/api/metricas/perdas?dias=30" "" "$TOKEN_ADMIN"
checar "GET /api/metricas/perdas (admin)" "200" "$STATUS"
AVARIA_MERCADO=$(echo "$BODY" | jq -r ".mercados[] | select(.mercadoId == \"${MERCADO_SLUG}\") | .avaria")
checar_valor "métrica de avaria do mercado de teste = 4" "$BODY" ".mercados[] | select(.mercadoId == \"${MERCADO_SLUG}\") | .avaria" "4"

# ---------------------------------------------------------------------------
# 8. RBAC — operador não pode fazer coisa de admin
# ---------------------------------------------------------------------------

secao "8. RBAC (admin x operador)"

chamar POST "/api/auth/usuarios" "{\"nome\":\"${OPERADOR_NOME}\",\"senha\":\"123456\",\"perfil\":\"operador\"}" "$TOKEN_ADMIN"
checar "admin cria um operador novo" "201" "$STATUS"

chamar POST "/api/auth/login" "{\"nome\":\"${OPERADOR_NOME}\",\"senha\":\"123456\"}"
checar "login do operador recém-criado" "200" "$STATUS"
TOKEN_OPERADOR=$(echo "$BODY" | jq -r '.token // empty')

if [ -n "$TOKEN_OPERADOR" ]; then
  chamar GET "/api/metricas/perdas" "" "$TOKEN_OPERADOR"
  checar "operador NÃO pode ver métricas (403 esperado)" "403" "$STATUS"

  chamar POST "/api/auth/usuarios" "{\"nome\":\"x-${TS}\",\"senha\":\"123456\"}" "$TOKEN_OPERADOR"
  checar "operador NÃO pode criar usuário (403 esperado)" "403" "$STATUS"

  chamar GET "/api/produtos" "" "$TOKEN_OPERADOR"
  checar "operador pode ver o catálogo normalmente" "200" "$STATUS"
else
  echo -e "  ${VERMELHO}✘${SEM_COR} não consegui logar como operador recém-criado — pulando os testes de RBAC"
  FALHAS=$((FALHAS + 2))
fi

# ---------------------------------------------------------------------------
# 9. Avarias, auditoria de login e relatórios (.docx) — feature 2026-10-13
# ---------------------------------------------------------------------------

secao "9. Avarias, login audit e relatórios"

ID_AVARIA=$(python3 -c "import uuid;print(uuid.uuid4())")
PAYLOAD_AVARIA="{
    \"empresaId\": \"frutap\", \"id\": \"${ID_AVARIA}\", \"mercadoId\": \"${MERCADO_SLUG}\", \"codigoEAN\": \"${EAN_NOVO}\",
    \"descricao\": \"Produto Teste Sync\", \"quantidade\": 2, \"motivo\": \"Caixa amassada no transporte\",
    \"operador\": \"Operador Teste\", \"nomeCliente\": \"Mercado Teste\", \"enderecoCliente\": \"Rua Teste, 123\"
  }"
chamar POST "/api/sync" "{\"id\": \"${ID_AVARIA}\", \"tipo\": \"avaria.registrar\", \"payload\": ${PAYLOAD_AVARIA}}" "$TOKEN_ADMIN"
checar "avaria.registrar" "200" "$STATUS"

chamar GET "/api/avarias" "" "$TOKEN_ADMIN"
checar "GET /api/avarias (admin)" "200" "$STATUS"
ITEM_AVARIA=$(echo "$BODY" | jq -c ".[] | select(.id == \"${ID_AVARIA}\")")
checar_valor "avaria registrada aparece na listagem (quantidade)" "$ITEM_AVARIA" '.quantidade' "2"
checar_valor "avaria registrada aparece na listagem (motivo)" "$ITEM_AVARIA" '.motivo' "Caixa amassada no transporte"

chamar GET "/api/avarias" "" "$TOKEN_OPERADOR"
checar "GET /api/avarias (operador também pode ver)" "200" "$STATUS"

chamar GET "/api/estoque/${MERCADO_SLUG}" "" "$TOKEN_ADMIN"
ITEM_ESTOQUE_AVARIA=$(echo "$BODY" | jq -c ".[] | select(.codigoEAN == \"${EAN_NOVO}\")")
checar_valor "saldo abatido pela avaria (96 - 2 = 94)" "$ITEM_ESTOQUE_AVARIA" '.produto.estoque.quantidadeAtual' "94"

chamar GET "/api/metricas/perdas?dias=30" "" "$TOKEN_ADMIN"
checar_valor "métrica de avaria soma movimentação (4) + avaria (2) = 6" "$BODY" ".mercados[] | select(.mercadoId == \"${MERCADO_SLUG}\") | .avaria" "6"

# Relatórios .docx — checar() só valida status HTTP a partir de $BODY (texto),
# e o corpo aqui é binário, então confere Content-Type direto com curl -w.
RESPOSTA_DOCX=$(curl -s -o /dev/null -w '%{http_code} %{content_type}' "${BASE_URL}/api/relatorios/avarias.docx" -H "Authorization: Bearer ${TOKEN_ADMIN}")
if echo "$RESPOSTA_DOCX" | grep -q "^200 .*wordprocessingml"; then
  echo -e "  ${VERDE}✔${SEM_COR} GET /api/relatorios/avarias.docx devolve .docx (${RESPOSTA_DOCX})"
  OK=$((OK + 1))
else
  echo -e "  ${VERMELHO}✘${SEM_COR} GET /api/relatorios/avarias.docx — esperado 200 + wordprocessingml, veio: ${RESPOSTA_DOCX}"
  FALHAS=$((FALHAS + 1))
fi

RESPOSTA_DOCX_PERDAS=$(curl -s -o /dev/null -w '%{http_code} %{content_type}' "${BASE_URL}/api/relatorios/perdas.docx" -H "Authorization: Bearer ${TOKEN_ADMIN}")
if echo "$RESPOSTA_DOCX_PERDAS" | grep -q "^200 .*wordprocessingml"; then
  echo -e "  ${VERDE}✔${SEM_COR} GET /api/relatorios/perdas.docx devolve .docx (admin) (${RESPOSTA_DOCX_PERDAS})"
  OK=$((OK + 1))
else
  echo -e "  ${VERMELHO}✘${SEM_COR} GET /api/relatorios/perdas.docx — esperado 200 + wordprocessingml, veio: ${RESPOSTA_DOCX_PERDAS}"
  FALHAS=$((FALHAS + 1))
fi

chamar GET "/api/relatorios/perdas.docx" "" "$TOKEN_OPERADOR"
checar "operador NÃO pode baixar relatório geral de perdas (403 esperado)" "403" "$STATUS"

chamar GET "/api/admin/logins" "" "$TOKEN_ADMIN"
checar "GET /api/admin/logins (admin)" "200" "$STATUS"
checar_valor "histórico de login tem pelo menos 1 entrada" "$BODY" 'length > 0' "true"

chamar GET "/api/admin/logins" "" "$TOKEN_OPERADOR"
checar "operador NÃO pode ver histórico de login (403 esperado)" "403" "$STATUS"

# "Limpar todos os logs" — as validações de RBAC e de confirmação sempre
# rodam; a exclusão de verdade só roda se TESTAR_LIMPAR_LOGS=true, porque
# ela apaga avarias/movimentações/pesquisas de TODA a empresa (não só os
# dados desta rodada de teste) — rodar isso por padrão destruiria histórico
# de verdade de quem executar o script contra um banco que importa.
chamar POST "/api/admin/logs/limpar" "{}" "$TOKEN_OPERADOR"
checar "operador NÃO pode limpar logs (403 esperado)" "403" "$STATUS"

chamar POST "/api/admin/logs/limpar" "{}" "$TOKEN_ADMIN"
checar "limpar logs sem confirmação retorna 400" "400" "$STATUS"

chamar POST "/api/admin/logs/limpar" "{\"confirmar\": \"errado\"}" "$TOKEN_ADMIN"
checar "limpar logs com confirmação errada retorna 400" "400" "$STATUS"

if [ "${TESTAR_LIMPAR_LOGS:-false}" = "true" ]; then
  chamar POST "/api/admin/logs/limpar" "{\"confirmar\": \"LIMPAR\"}" "$TOKEN_ADMIN"
  checar "limpar logs com confirmação correta (DESTRUTIVO)" "200" "$STATUS"
  checar_valor "avarias removidas >= 1" "$BODY" '.removidos.avarias >= 1' "true"

  chamar GET "/api/avarias" "" "$TOKEN_ADMIN"
  checar_valor "avarias zeradas após limpar logs" "$BODY" 'length' "0"
else
  echo -e "  ${AMARELO}…${SEM_COR} POST /api/admin/logs/limpar (confirmação correta) — PULADO"
  echo -e "    Esse é destrutivo pra EMPRESA INTEIRA (apaga avarias/movimentações/pesquisas de todos os mercados)."
  echo -e "    Rode com TESTAR_LIMPAR_LOGS=true ./test_api.sh só contra um banco descartável pra exercitar de verdade."
fi

# ---------------------------------------------------------------------------
# Resumo
# ---------------------------------------------------------------------------

echo
echo "============================================================"
if [ "$FALHAS" -eq 0 ]; then
  echo -e "${VERDE}TUDO CERTO — ${OK} verificações passaram, 0 falharam.${SEM_COR}"
  echo "Mercado/produto de teste desta rodada: ${MERCADO_SLUG} / EAN ${EAN_NOVO}"
  echo "============================================================"
  exit 0
else
  echo -e "${VERMELHO}ATENÇÃO — ${OK} passaram, ${FALHAS} FALHARAM.${SEM_COR}"
  echo "Mercado/produto de teste desta rodada: ${MERCADO_SLUG} / EAN ${EAN_NOVO}"
  echo "Revise as linhas marcadas com ✘ acima."
  echo "============================================================"
  exit 1
fi
