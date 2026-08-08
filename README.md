# Sistema de Finanças Pessoais

Aplicativo web local (roda no seu navegador) para controle financeiro completo.

## Recursos

- 📊 Dashboard com patrimônio total (ativos − dívidas) e gráficos
- 💳 Cartões de crédito com compras parceladas e fatura mensal
- 🏦 Contas (Nubank, Mercado Pago etc.)
- 🪙 Caixinhas e reserva de emergência
- 📈 Investimentos (ações, FIIs, cripto, renda fixa)
- 🔄 Cotações em tempo real (Yahoo Finance) + estimativa de dividendos/mês
- 🤖 IA que lê print de extrato bancário e lança as transações (Google Gemini)
- 🏍️ Financiamentos e parcelas com controle de progresso
- 📅 Contas recorrentes
- 💸 Transações com categorias e filtros por mês
- 📄 Importação e exportação em XML
- ☁️ Sincronização com Firebase Firestore (ou arquivo local)

## Como rodar

### Opção 1 (Windows) — clique duplo

Dê dois cliques em `run.bat`. Na primeira execução ele cria um ambiente
virtual e instala as dependências sozinho. O navegador abre sozinho em
`http://127.0.0.1:5000`.

### Opção 2 — manual

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main.py
```

Abra `http://127.0.0.1:5000`.

## Armazenamento

O sistema usa **Firebase Firestore** se houver credenciais, caso contrário usa
um **arquivo local** (`data/local_db.json`) — assim funciona imediatamente.

### Ativar o Firebase

1. Crie um projeto em https://console.firebase.google.com (plano gratuito "Spark" é suficiente).
2. No projeto, ative **Firestore Database** em "Build → Firestore Database".
3. Vá em "Configurações do projeto (engrenagem) → Contas de serviço".
4. Clique em **Gerar nova chave privada** e baixe o JSON.
5. Renomeie o arquivo para `serviceAccountKey.json` e coloque na pasta
   `credentials/` do projeto.
6. Reinicie o sistema. O badge no topo passará de "Arquivo local" para "Firebase".

Para migrar os dados já cadastrados para a nuvem: em **Dados → Exportar**,
troque as credenciais, reinicie, e em **Dados → Importar** suba o XML gerado.

## Investimentos em tempo real

Na página **Investimentos**, clique em **↻ Atualizar cotações**. O sistema
consulta a API gratuita do **Yahoo Finance** (sem chave) e atualiza:

- **Preço atual** de ações/FIIs/ETFs brasileiros (ticker ganha o sufixo `.SA`)
  e de cripto (BTC, ETH etc., convertidos de USD para BRL).
- **Dividendos estimados/mês** por ativo (média dos últimos 12 meses × suas cotas)
  e o total no resumo do topo.
- A **data da última cotação** aparece ao lado do preço.

Se um ticker não existir (ex.: renda fixa sem código de mercado), o ativo é
pulado sem quebrar os demais. A rede é obrigatória para o recurso.

## IA que lê extrato bancário (Gemini)

Na página **Transações**, há o card **📷 Ler extrato com IA (print)**:

1. Envie um **print/screenshot do extrato** (PNG/JPG/WebP ou PDF).
2. A IA (Google Gemini) extrai as movimentações e mostra uma **nota** com o que
   foi detectado: data, descrição, categoria, tipo e valor.
3. Revise, desmarque o que não quiser e clique em **✓ Lançar selecionadas** —
   as transações são criadas na conta escolhida.

Para ativar, você precisa de uma **chave da API do Google Gemini** (gratuita em
https://aistudio.google.com → "Get API key"). Há 3 formas de configurar:

1. Na página **Dados → IA de leitura de extrato**, cole a chave e salve
   (grava em `credentials/gemini_key.txt`); ou
2. Defina a variável de ambiente `GEMINI_API_KEY`; ou
3. Crie o arquivo `credentials/gemini_key.txt` com a chave dentro.

Modelo padrão: `gemini-2.0-flash` (mude com a variável `GEMINI_MODEL`).
Se a cota do modelo padrão estiver esgotada (HTTP 429), o app tenta
automaticamente os modelos de reserva (`GEMINI_MODEL_FALLBACKS`, padrão:
`gemini-flash-latest,gemini-flash-lite-latest,gemini-2.5-flash`). Se todos
esgotarem, aguarde o reset diário da cota gratuita.

## Importar e exportar XML

- **Exportar:** Dados → Baixar XML (gera backup completo).
- **Importar:** Dados → Importar, escolhendo **acrescentar** ou **substituir**.
- Há um exemplo em `example_export.xml` para testar a importação.

Também é possível importar **planilhas Excel (.xlsx)** com uma aba por coleção
(`Contas`, `Cartões`, `Compras`, `Caixinhas`, `Investimentos`, `Financiamentos`,
`Recorrentes`, `Transações`, `Transferências`, `Patrimônio`), primeira linha
como cabeçalho. Abas e colunas aceitam português (ex.: `Nome`, `Saldo`, `Valor`,
`Data`), datas em `AAAA-MM-DD` ou `DD/MM/AAAA`, e valores com vírgula
(ex.: `1.200,50`). Referências como `Conta`/`Cartão` podem usar o nome.
Arquivos `.xls` antigos devem ser salvos como `.xlsx` antes de importar.

O modo **acrescentar** não duplica: contas/cartões/caixinhas com o mesmo nome,
compras e transações iguais são ignorados em uploads repetidos, e referências
(compra→cartão, transação→conta) são resolvidas automaticamente. Assim, você
pode **alimentar o sistema aos poucos**, subindo vários XMLs ao longo do tempo
— inclusive gerados por ChatGPT no formato descrito em `FORMATO_XML.md`.

### Migração do backup antigo (FinancialSystem)

Se você tem o XML do sistema antigo (`financeiro_backup_*.xml`), use:

```bash
.venv\Scripts\python migrate_legacy.py "C:\caminho\do\backup.xml"
```

O conversor mapeia contas, caixinhas, reservas, metas, investimentos,
despesas fixas, empréstimos/financiamentos e a fatura do cartão, e é seguro
rodar mais de uma vez (não duplica).

## Testes

```bash
.venv\Scripts\python -m unittest test_app -v
```

## Estrutura

```
main.py              # entrada (inicia o servidor e abre o navegador)
run.bat              # atalho do Windows
app/                 # código do servidor (Flask)
  routes.py          # páginas + API
  storage.py         # Firestore ou arquivo local
  xml_utils.py       # import/export XML (com deduplicação)
  config.py          # caminhos e categorias
templates/           # páginas HTML
static/              # CSS e JS
credentials/         # coloque aqui o serviceAccountKey.json
data/local_db.json   # armazenamento local (fallback)
example_export.xml   # exemplo de XML para importação
FORMATO_XML.md       # esquema XML para gerar dados (ex.: via ChatGPT)
migrate_legacy.py    # conversor do backup antigo
test_app.py          # testes automáticos
```
