# Esquema XML do sistema de finanças

Formato usado para **exportar** (Dados → Baixar XML) e **importar** dados.
Você pode gerar arquivos nesse formato (até por ChatGPT) e subir em
**Dados → Importar** para alimentar o sistema, além de cadastrar pela tela.

## Regras

- Elemento raiz: `<finance-export>`.
- Cada coleção é um bloco filho: `accounts`, `cards`, `card_purchases`,
  `boxes`, `investments`, `financings`, `recurring`, `transactions`, `networth`.
- Cada registro é um `<item>` (o atributo `id` é opcional em uploads novos).
- Valores decimais usam ponto (ex.: `9.53`).
- Datas no formato `AAAA-MM-DD`.
- Campos numéricos que não existem podem ser omitidos (valem 0).
- No modo "acrescentar", registros iguais já existentes são **ignorados**
  (não duplicam). Referências como `card_id` e `account_id` podem apontar
  para o `id` de outro bloco do MESMO arquivo, ou para o **nome** da conta/
  cartão — o sistema resolve.

## Modelos completos

```xml
<?xml version="1.0" encoding="UTF-8"?>
<finance-export version="1.0" date="2026-08-07" currency="BRL">

  <!-- Contas bancárias -->
  <accounts>
    <item id="acc-nubank">
      <name>Nubank</name>
      <institution>Nubank</institution>
      <type>Conta corrente</type>   <!-- ou Poupança, Carteira digital -->
      <balance>328.33</balance>
    </item>
  </accounts>

  <!-- Cartões de crédito -->
  <cards>
    <item id="card-nubank">
      <name>Nubank Ultravioleta</name>
      <brand>Mastercard</brand>
      <limit>10000</limit>
      <due_day>10</due_day>
    </item>
  </cards>

  <!-- Compras no cartão (parceladas ou à vista) -->
  <card_purchases>
    <item>
      <card_id>card-nubank</card_id>   <!-- ou o NOME do cartão -->
      <date>2026-07-20</date>
      <description>Moto peças</description>
      <category>Transporte</category>
      <amount>1200</amount>           <!-- valor total da compra -->
      <installments>6</installments>  <!-- 1 = à vista -->
    </item>
  </card_purchases>

  <!-- Caixinhas (inclui reserva de emergência e metas) -->
  <boxes>
    <item>
      <name>Reserva de emergência</name>
      <target>30000</target>
      <balance>12500</balance>
      <is_emergency>true</is_emergency>
    </item>
  </boxes>

  <!-- Investimentos -->
  <investments>
    <item>
      <name>MXRF11</name>
      <ticker>MXRF11</ticker>
      <type>FII</type>               <!-- Ação, FII, Cripto, Renda fixa, ETF -->
      <quantity>300</quantity>
      <avg_price>9.5</avg_price>
      <current_price>10.1</current_price>
    </item>
  </investments>

  <!-- Financiamentos / parcelas longas -->
  <financings>
    <item>
      <name>Fan 160 2025 - Ruivona</name>
      <category>Transporte</category>
      <total>13000</total>
      <monthly_value>650</monthly_value>
      <installments_total>20</installments_total>
      <paid>0</paid>
      <due_day>5</due_day>
      <start_date>2026-08-01</start_date>
    </item>
  </financings>

  <!-- Contas recorrentes -->
  <recurring>
    <item>
      <name>Internet</name>
      <category>Moradia</category>
      <amount>99.9</amount>
      <frequency>Mensal</frequency>
      <due_day>15</due_day>
      <account_id>acc-nubank</account_id>  <!-- ou o NOME da conta -->
      <active>true</active>
    </item>
  </recurring>

  <!-- Transações avulsas -->
  <transactions>
    <item>
      <date>2026-08-05</date>
      <description>Salário</description>
      <amount>5000</amount>
      <type>income</type>            <!-- expense ou income -->
      <category>Salário</category>
      <account_id>acc-nubank</account_id>
      <method>Pix</method>
    </item>
  </transactions>

  <!-- Histórico de patrimônio (gráfico) -->
  <networth>
    <item>
      <date>2026-08-01</date>
      <total>18320</total>
    </item>
  </networth>

</finance-export>
```

## Categorias válidas

`Moradia`, `Alimentação`, `Transporte`, `Lazer`, `Saúde`, `Educação`,
`Cartão de crédito`, `Investimentos`, `Salário`, `Freela`, `Outros`.
(Se vier outra, o sistema aceita mesmo assim.)

## Dica para alimentar via ChatGPT

Peça ao ChatGPT para gerar o XML nesse formato com seus dados e envie o
arquivo em **Dados → Importar → acrescentar**. Depois de importar, use
**Dados → Baixar XML** para ter sempre o backup completo com os ids reais.

## Ou importe por planilha Excel (.xlsx)

Para reduzir erros de formatação, você também pode pedir ao ChatGPT (ou
montar você mesmo) um **Excel** em vez de XML. Crie uma aba por coleção e a
primeira linha com o cabeçalho:

| Aba | Cabeçalho esperado (aceita português) |
|---|---|
| `Contas` | `id`, `Nome`, `Instituição`, `Tipo`, `Saldo` |
| `Cartões` | `id`, `Nome`, `Bandeira`, `Limite`, `Dia do Vencimento` |
| `Compras` | `Cartão` (id ou nome), `Data`, `Descrição`, `Categoria`, `Valor`, `Parcelas` |
| `Caixinhas` | `Nome`, `Meta`, `Saldo`, `Reserva de emergência` (true/false) |
| `Investimentos` | `Nome`, `Ticker`, `Tipo`, `Quantidade`, `Preço médio`, `Preço atual` |
| `Financiamentos` | `Nome`, `Categoria`, `Total`, `Valor mensal`, `Parcelas total`, `Pago`, `Dia do Vencimento`, `Data` |
| `Recorrentes` | `Nome`, `Categoria`, `Valor`, `Frequência`, `Dia do Vencimento`, `Conta`, `Ativo` |
| `Transações` | `Data`, `Descrição`, `Valor`, `Tipo` (expense/income), `Categoria`, `Conta`, `Método` |
| `Transferências` | `Data`, `Descrição`, `Valor`, `De tipo`, `De`, `Para tipo`, `Para` |
| `Patrimônio` | `Data`, `Total` |

Regras: valores com vírgula são aceitos (`1.200,50`), datas podem ser
`AAAA-MM-DD` ou `DD/MM/AAAA`, e a coluna `id` é opcional. Funciona igual ao
XML — no modo acrescentar não duplica e `Conta`/`Cartão` podem ser o nome.
