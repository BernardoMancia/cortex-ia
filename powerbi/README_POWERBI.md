# Kit Oficial Microsoft Power BI — Córtex IA Star-Schema B3 (`v0.2.0`)

Este diretório reúne os artefatos oficiais de Business Intelligence do **Córtex IA** para modelagem analítica institucional no **Microsoft Power BI Desktop** e **Power BI Service**, operando sobre o **Star-Schema Somente-Leitura** (`/api/bi/v1/*`).

---

## 1. Arquivos do Kit

| Arquivo | Finalidade |
|---|---|
| `cortex_ia_prod.pbids` | Fonte de dados Power BI apontando por padrão para `http://localhost:8003/api/bi/v1/metadata` (Produção). |
| `cortex_ia_homolog.pbids` | Fonte de dados Power BI apontando por padrão para `http://localhost:9003/api/bi/v1/metadata` (Homologação). |
| `queries_star_schema.pq` | Consultas Power Query (`M`) tipadas com parâmetros `ServerBaseUrl`, `BiApiKey` e função `CortexFetch`. |
| `dax_measures.dax` | Catálogo de 32 medidas DAX institucionais (Sharpe, Sortino, Drawdown, Profit Factor, Termômetro PF R$ 20k, Liquidação D+2). |
| `cortex_dark_theme.json` | Tema Dark Institucional Córtex IA pronto para importação no Power BI Desktop. |

> **Dica:** Pelo Cockpit Web (`📊 Power BI & Gráficos`), clique em **`⬇️ Baixar Conector Power BI (.pbids)`** para receber automaticamente o `.pbids` já preenchido com o `Host` exato onde você está autenticado.

---

## 2. Como Gerar o Token Somente-Leitura (`ctx_bi_...`)

1. Acesse o Cockpit Web do **Córtex IA** com seu usuário e senha (e 2FA TOTP, se ativo).
2. Abra a aba **`📊 Power BI & Gráficos`**.
3. No formulário **Gerar Novo Token Somente-Leitura Power BI**, preencha:
   - **Nome / Rótulo:** ex.: `Power BI Desktop Institucional`.
   - **Escopo de Carteira:** escolha `Todas as minhas carteiras` ou restrinja a uma carteira específica (Anti-IDOR).
   - **Validade:** `30`, `90`, `365` dias ou `Sem expiração`.
   - **Lista de IPs / CIDR (Opcional):** restrinja os IPs autorizados a consumir a API BI.
4. Clique em **`🔐 Emitir Token Somente-Leitura (Step-Up)`** e confirme sua senha/2FA.
5. **Copie imediatamente** o token `ctx_bi_...` exibido na tela — o servidor armazena apenas o hash criptográfico `HMAC-SHA256` e jamais voltará a exibir o segredo bruto.

---

## 3. Conectando no Power BI Desktop

1. Abra o **Power BI Desktop** (ou dê duplo clique no arquivo `.pbids` baixado).
2. Abra **Transformar Dados** (**Power Query Editor**).
3. Crie os 3 parâmetros abaixo em **Gerenciar Parâmetros** (ou cole os blocos equivalentes de `queries_star_schema.pq` no **Editor Avançado**):
   - `ServerBaseUrl` (Texto): URL base do seu servidor Córtex IA sem barra final (ex.: `http://localhost:8003` em Produção local, `http://localhost:9003` em Homologação local, ou o endereço HTTPS do seu domínio corporativo).
   - `BiApiKey` (Texto): cole o token `ctx_bi_...` gerado na etapa anterior.
   - `WalletIdFilter` (Texto, opcional): deixe vazio `""` para trazer todas as carteiras permitidas ao token ou informe o ID numérico da carteira desejada.
4. Crie as funções auxiliares `CortexFetch` e `CortexRecordsToTable` e as consultas tipadas de `queries_star_schema.pq`:
   - Dimensões: `dim_wallets`, `dim_assets_b3`, `dim_calendar_b3`
   - Fatos: `fact_equity`, `fact_positions`, `fact_trades`, `fact_decisions`, `fact_cashflow_d2`, `fact_market_radar`
   - Consolidado Executivo: `kpi_summary`
5. Quando o Power BI solicitar credenciais de acesso Web para `ServerBaseUrl`, selecione **Anônimo** (*Anonymous*), pois a autenticação criptográfica já trafega via cabeçalho HTTP `Authorization: Bearer ctx_bi_...` encapsulado em `Web.Contents(..., [RelativePath = ..., Headers = ...])`.

---

## 4. Relacionamentos do Modelo Star-Schema (1:N Direção Única)

No modo **Exibição de Modelo** do Power BI Desktop, configure os seguintes relacionamentos **Um-para-Muitos (`1:*`)** com filtro cruzado simples das dimensões para as tabelas fato:

1. **`dim_wallets[wallet_id]` (1) -> (*)**
   - `fact_equity[wallet_id]`
   - `fact_positions[wallet_id]`
   - `fact_trades[wallet_id]`
   - `fact_decisions[wallet_id]`
   - `fact_cashflow_d2[wallet_id]`
   - `kpi_summary[wallet_id]`
2. **`dim_assets_b3[ticker]` (1) -> (*)**
   - `fact_positions[ticker]`
   - `fact_trades[ticker]`
   - `fact_market_radar[ticker]`
   - `fact_decisions[ticker]` *(observação: decisões macro de regime usam `ticker = "MARKET"`)*
3. **`dim_calendar_b3[date]` (1) -> (*)**
   - `fact_equity[date]`
   - `fact_trades[date]`
   - `fact_decisions[date]`
   - `fact_cashflow_d2[date]`
   - *(Relacionamento inativo secundário opcional)*: `dim_calendar_b3[date]` -> `fact_cashflow_d2[settlement_date]`

Marque `dim_calendar_b3` como **Tabela de Datas Oficial** (*Mark as date table*) apontando para a coluna `date`.

---

## 5. Importando Tema Dark & Medidas DAX

1. No Power BI Desktop, acesse **Exibição -> Temas -> Procurar temas** e selecione `powerbi/cortex_dark_theme.json`.
2. Crie uma tabela de medidas dedicada (ex.: `_Medidas_Cortex`) e adicione as expressões de `powerbi/dax_measures.dax`.
3. Todas as tabelas incluem as colunas `environment` (`production` ou `homologation`) e `is_simulation` (`true` ou `false`), impedindo qualquer confusão visual entre dados reais B3 e simulações de homologação.

---

## 6. Garantias de Segurança Institucional

- **Firewall Somente-Leitura Intransponível:** Tokens `ctx_bi_...` são rejeitados com `HTTP 403 Forbidden` + evento auditável `BI_TOKEN_SCOPE_VIOLATION` caso sejam utilizados em qualquer endpoint fora de `/api/bi/v1/*` (como ordens, saques, carteiras, pânico ou administração).
- **Isolamento Anti-IDOR:** Cada token enxerga exclusivamente os dados do usuário proprietário e, quando emitido com escopo de carteira fixa, bloqueia consultas a qualquer outro `wallet_id`.
- **Revogação Instantânea:** Clicar em **Revogar** na aba `📊 Power BI & Gráficos` invalida imediatamente o token em todas as rotas analíticas (`HTTP 401 Unauthorized`).
