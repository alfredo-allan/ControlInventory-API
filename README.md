# Backend — API de persistência do ControlInventory

API Flask que recebe a fila de sincronização do app (`client/src/lib/syncQueue.ts`)
e persiste tudo em banco de dados real — SQLite por padrão (dev local e no
deploy atual, plano Free do PythonAnywhere), com suporte a MySQL sem
nenhuma mudança de código se um dia migrar pro plano Developer (ver seção
3b). Em produção, esse mesmo Flask também serve o build do frontend
(`client/dist`), pra rodar tudo num único web app.

Esse backend foi desenhado pra receber o payload que o cliente offline-first
**já produz hoje**, sem nenhuma camada de tradução: o endpoint central
`POST /api/sync` aceita exatamente o formato `{id, tipo, payload}` que o
`syncQueue.ts` já monta. Ir ao ar é, na prática, trocar a função simulada
`enviarParaServidor` desse arquivo por uma chamada `fetch` real — nada na
arquitetura do cliente precisa mudar.

## Stack

Flask 3 + Flask-SQLAlchemy + Flask-Migrate (Alembic) + Flask-CORS + PyJWT +
PyMySQL. Escolhida por ser o que o PythonAnywhere suporta nativamente via
WSGI sem configuração extra (ver seção de deploy abaixo).

## Estrutura

```
backend/
  app/
    __init__.py          # app factory (create_app)
    config.py             # lê tudo de variáveis de ambiente
    extensions.py          # db, migrate, cors
    models.py              # schema (espelha os tipos TS do frontend)
    auth/                  # login, JWT, RBAC (admin/operador)
    sync/                  # POST /api/sync — o endpoint central
    catalogo/               # GET /api/produtos, /api/mercados
    estoque/                 # GET /api/estoque/<mercado>, /movimentacoes, /pesquisas-semanais
    metricas/                 # GET /api/metricas/perdas (admin, base do painel da Fase 3)
  migrations/                 # histórico de migração (Alembic) — gerado por `flask db`
  seed_data/frutap.json        # mesmo catálogo que o frontend usa, copiado
  seed.py                       # popula empresa + catálogo + 1º usuário admin
  wsgi.py                        # ponto de entrada (local E PythonAnywhere)
  requirements.txt
  .env.example
  frontend_dist/                 # build do frontend (client/dist copiado pra cá) — só existe
                                  # em produção; servido pelo próprio Flask (ver seção 3, passo 9)
```

## 1. Rodando local (SQLite, sem precisar de MySQL)

```bash
cd backend
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # os defaults já funcionam pra rodar local

export FLASK_APP=wsgi.py           # Windows (PowerShell): $env:FLASK_APP="wsgi.py"
flask db upgrade                   # cria as tabelas (SQLite: backend/dev.db)
python seed.py                     # popula o catálogo (82 produtos / 14 mercados) + usuário admin

flask run --port 5050
```

Testando rapidamente:

```bash
curl http://127.0.0.1:5050/api/health
curl -X POST http://127.0.0.1:5050/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"nome":"admin","senha":"troque-essa-senha"}'
```

O login devolve um `token` — use em todas as outras rotas com o header
`Authorization: Bearer <token>`.

## 2. Mudando o schema depois (sempre que mexer em `app/models.py`)

```bash
export FLASK_APP=wsgi.py
flask db migrate -m "descrição curta da mudança"
flask db upgrade
```

Nunca editar tabelas na mão — sempre pelo fluxo de migração acima, tanto
local quanto no PythonAnywhere (senão os dois ambientes ficam com schemas
diferentes e divergem).

## 3. Deploy no PythonAnywhere — plano Free (decisão tomada em 2026-10-13)

Confirmado com o usuário: a conta é **plano Free**. Contas gratuitas
criadas a partir de janeiro de 2026 não têm mais MySQL nem tarefa
agendada — então o caminho escolhido foi: **SQLite em produção** (o
`app/config.py` já cai pra SQLite sozinho se as variáveis de MySQL não
estiverem definidas, nenhuma mudança de código precisou ser feita) e **o
próprio Flask servindo o frontend** (`client/dist`), pra não precisar de
um segundo serviço de hospedagem — o plano Free só permite 1 web app por
conta, então "tudo num site só" é o que funciona sem custo.

> ⚠️ Antes de seguir, confira na aba **"Databases"** do painel se por
> acaso aparece uma opção de MySQL (contas abertas antes de 15/jan/2026
> mantêm o acesso). Se aparecer, dá pra usar o caminho com MySQL (seção
> 3b abaixo) em vez do SQLite — mas o padrão assumido aqui é que não tem.

**Limitações conhecidas do plano Free, pra não ter surpresa depois:**
- O site expira depois de ~3 meses sem acesso ao painel — precisa entrar
  e clicar em "Run until 3 months from today" periodicamente.
- Sem tarefa agendada — nenhum backup automático do `dev.db`. Backup vira
  responsabilidade manual (baixar o arquivo `dev.db` de vez em quando pela
  aba "Files", especialmente antes de usar "Limpar todos os logs" no Admin).
- SQLite é um arquivo único — ótimo pra esse volume de dados (uma ou
  poucas empresas, algumas centenas de produtos/movimentações), mas não
  seria a escolha certa se o projeto crescer pra múltiplos clientes com
  volume alto simultâneo. Se isso acontecer, é quando faz sentido
  reconsiderar o plano Developer + MySQL (seção 3b).

### Passo a passo

1. **Criar a conta** (se ainda não tiver) em pythonanywhere.com — isso é
   login/senha da sua própria conta, então é um passo que só você pode
   fazer; eu não tenho como entrar por você.

2. **Gerar uma `SECRET_KEY` de produção** — já gerei uma pra você usar
   (não é segredo meu, é só um valor aleatório — troque se quiser gerar a
   sua própria com `python3 -c "import secrets; print(secrets.token_hex(32))"`):
   ```
   d72f55538c58098bb8845b5b339baceca092aa4f74b12191caf14fe5bbcfe5c3
   ```

3. **Escolher a senha do usuário `admin`** — é você quem decide, não eu.
   Guarde em algum lugar seguro (gerenciador de senhas) — vai usar no
   passo 7.

4. **Subir o código** — pelo console **Bash** do PythonAnywhere (aba
   "Consoles" → "Bash"):
   ```bash
   git clone <url-do-repositorio-se-tiver-um>
   # ou: aba "Files" → "Upload a file" → suba um .zip do backend/ e extraia:
   # unzip ControlInventory-API.zip -d controlinventory-api
   cd controlinventory-api    # ajuste conforme o nome real da pasta
   ```

5. **Criar o virtualenv e instalar as dependências**:
   ```bash
   mkvirtualenv --python=/usr/bin/python3.10 controlinventory-venv
   pip install -r requirements.txt
   ```

6. **Criar o web app** — painel "Web" → "Add a new web app" → **"Manual
   configuration"** (não "Flask" — o wizard monta a estrutura de outro
   jeito) → mesma versão de Python do virtualenv → na seção "Virtualenv",
   aponte pro caminho do virtualenv do passo 5 (normalmente
   `/home/seuusuario/.virtualenvs/controlinventory-venv`).

7. **Editar o arquivo WSGI** (link na seção "Code" da página do web app,
   algo como `/var/www/seuusuario_pythonanywhere_com_wsgi.py`). Apague o
   conteúdo de exemplo e troque por (ajustando `seuusuario` e os valores
   que você escolheu nos passos 2/3):
   ```python
   import sys
   import os

   caminho_projeto = "/home/seuusuario/controlinventory-api"
   if caminho_projeto not in sys.path:
       sys.path.insert(0, caminho_projeto)

   os.environ["SECRET_KEY"] = "d72f55538c58098bb8845b5b339baceca092aa4f74b12191caf14fe5bbcfe5c3"
   os.environ["CORS_ORIGINS"] = "https://seuusuario.pythonanywhere.com"
   os.environ["FLASK_ENV"] = "production"
   # Sem DB_HOST/DB_USER/DB_PASSWORD/DB_NAME de propósito — cai em SQLite
   # sozinho (ver app/config.py). Se um dia migrar pro plano com MySQL,
   # é só adicionar essas 4 variáveis aqui, nada mais muda.

   from wsgi import application
   ```

8. **Rodar as migrations e o seed** — console Bash, dentro do virtualenv
   (`workon controlinventory-venv`):
   ```bash
   cd ~/controlinventory-api
   export FLASK_APP=wsgi.py
   export FLASK_ENV=production
   flask db upgrade
   python seed.py --admin-senha "a-senha-que-voce-escolheu-no-passo-3"
   ```

9. **Buildar o frontend e copiar pra dentro do backend** — isso roda na
   SUA máquina (ou em qualquer lugar com Node), não no PythonAnywhere:
   ```bash
   cd ControlInventory          # a pasta do frontend, irmã da do backend
   VITE_API_URL=https://seuusuario.pythonanywhere.com npm run build:front
   ```
   Depois suba o conteúdo de `client/dist/` pra dentro de
   `controlinventory-api/frontend_dist/` no PythonAnywhere (aba "Files",
   arrastando os arquivos, ou via `git` se o repositório incluir o build).
   **Repita este passo toda vez que o frontend mudar** — o backend só
   serve o que estiver fisicamente nessa pasta.

10. **Recarregar o web app** — botão verde "Reload" no topo da página do
    web app. Necessário depois de QUALQUER mudança de código, WSGI, ou
    depois de atualizar `frontend_dist/`.

11. **Testar**:
    - `https://seuusuario.pythonanywhere.com/api/health` → `{"status":"ok"}`
    - `https://seuusuario.pythonanywhere.com/` → a Home animada deve
      carregar normalmente, e o login deve funcionar com o usuário/senha
      do passo 8.

### Atualizando depois do primeiro deploy

```bash
# backend:
cd ~/controlinventory-api
git pull                                     # ou reenviar os arquivos alterados
workon controlinventory-venv
pip install -r requirements.txt              # só se requirements.txt mudou
flask db upgrade                             # só se o schema mudou

# frontend (rodar localmente, depois subir o resultado):
# VITE_API_URL=https://seuusuario.pythonanywhere.com npm run build:front
# → copiar client/dist/* pra controlinventory-api/frontend_dist/
```
...e clicar em "Reload" no painel Web de novo. Sem o reload, o PythonAnywhere
continua servindo o código antigo.

## 3b. Alternativa — plano Developer (MySQL), se decidir upgradar depois

O código já suporta os dois caminhos sem mudança nenhuma — `app/config.py`
usa MySQL automaticamente se `DB_HOST`/`DB_USER`/`DB_PASSWORD`/`DB_NAME`
estiverem definidos, e cai pra SQLite se não estiverem. Pra migrar:

1. Painel → aba "Databases" → criar o banco MySQL, anotar host/usuário/senha/nome.
2. No WSGI (passo 7 acima), acrescentar as 4 variáveis:
   ```python
   os.environ["DB_HOST"] = "seuusuario.mysql.pythonanywhere-services.com"
   os.environ["DB_USER"] = "seuusuario"
   os.environ["DB_PASSWORD"] = "a-senha-que-voce-definiu-na-aba-databases"
   os.environ["DB_NAME"] = "seuusuario$controlinventory"
   ```
3. Rodar `flask db upgrade` + `python seed.py` de novo — contra o MySQL
   dessa vez (precisa repetir mesmo que já tenha rodado contra o SQLite;
   são bancos diferentes). Se quiser migrar os DADOS do SQLite pro MySQL
   em vez de recomeçar do zero, isso precisaria de um script à parte —
   não existe um pronto hoje, me avise se chegar nesse ponto.
4. Reload no web app.

A dica de migrar os dados existentes do SQLite (histórico de produtos,
avarias, etc.) pro MySQL novo, se um dia isso for necessário, fica como
trabalho futuro — hoje não existe um script pronto pra isso.
