"""
AnáliseUp — Agente de Análise (Railway)
Configuração via variáveis de ambiente:
  SHOPEE_PARTNER_ID
  SHOPEE_PARTNER_KEY
  ANTHROPIC_API_KEY
  MONDAY_API_KEY
  MONDAY_BOARD_ID
  TOKENS_JSON  ← conteúdo do tokens.json em base64 ou JSON direto
"""

import hmac
import hashlib
import time
import json
import os
import requests
import anthropic
from datetime import datetime

# ─── CONFIGURAÇÕES (via env vars) ────────────────────────────
SHOPEE_PARTNER_ID   = int(os.environ.get("SHOPEE_PARTNER_ID", "2036153"))
SHOPEE_PARTNER_KEY  = os.environ.get("SHOPEE_PARTNER_KEY", "shpk716d4e664d5272536859716b4e7a657a6b7a7570454e4863774f76465259")
ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
MONDAY_API_KEY      = os.environ.get("MONDAY_API_KEY", "")
MONDAY_BOARD_ID     = int(os.environ.get("MONDAY_BOARD_ID", "9943771778"))
SHOPEE_BASE_URL     = "https://partner.shopeemobile.com"

# Clientes — shop_id preenchido após autorização OAuth de cada loja
CLIENTES = {
    "rosaliz":         {"monday_id": 12147461757, "shop_id": 1475997326},
    "almavoga":        {"monday_id": 12147446749, "shop_id": 1230039734},
    "aorbe":           {"monday_id": 12147461816, "shop_id": 1706475653},
    "neblina":         {"monday_id": 12147461765, "shop_id": None},
    "juninho":         {"monday_id": 12147461817, "shop_id": None},
    "donizete":        {"monday_id": 12147461632, "shop_id": None},
    "kboutique":       {"monday_id": 12147461818, "shop_id": 1141347861},
    "marcos_republik": {"monday_id": 12147461766, "shop_id": 1614778372},
    "cristiano_joias": {"monday_id": 12147520757, "shop_id": None},
    "amigo_marlene":   {"monday_id": 12182372218, "shop_id": None},
    # Loja de teste (B_CLOUSET)
    "b_clouset":       {"monday_id": None, "shop_id": 678623539},
}

# ─── TOKENS ──────────────────────────────────────────────────

def carregar_tokens():
    """
    Tenta carregar tokens de 3 fontes (em ordem):
    1. Variável de ambiente TOKENS_JSON
    2. Arquivo tokens.json na pasta atual
    3. Retorna dicionário vazio
    """
    tokens_env = os.environ.get("TOKENS_JSON", "")
    if tokens_env:
        try:
            return json.loads(tokens_env)
        except json.JSONDecodeError:
            print("⚠️  TOKENS_JSON inválido — ignorando")

    try:
        with open("tokens.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def salvar_tokens(nome, shop_id, access_token, refresh_token):
    tokens = carregar_tokens()
    tokens[nome] = {
        "shop_id": shop_id,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "atualizado_em": int(time.time()),
    }
    with open("tokens.json", "w") as f:
        json.dump(tokens, f, indent=2)

# ─── SHOPEE API ───────────────────────────────────────────────

def _sign(path, timestamp, access_token="", shop_id=0):
    if shop_id:
        base = f"{SHOPEE_PARTNER_ID}{path}{timestamp}{access_token}{shop_id}"
    else:
        base = f"{SHOPEE_PARTNER_ID}{path}{timestamp}"
    return hmac.new(SHOPEE_PARTNER_KEY.encode(), base.encode(), hashlib.sha256).hexdigest()


def shopee_get(path, access_token, shop_id, params=None):
    ts = int(time.time())
    sign = _sign(path, ts, access_token, shop_id)
    base_params = dict(
        partner_id=SHOPEE_PARTNER_ID,
        timestamp=ts,
        sign=sign,
        access_token=access_token,
        shop_id=shop_id,
    )
    if params:
        base_params.update(params)
    r = requests.get(SHOPEE_BASE_URL + path, params=base_params, timeout=30)
    return r.json()


def renovar_token(refresh_token, shop_id):
    """Renova o access_token usando o refresh_token."""
    ts = int(time.time())
    path = "/api/v2/auth/access_token/get"
    sign = _sign(path, ts)
    body = {
        "refresh_token": refresh_token,
        "shop_id": shop_id,
        "partner_id": SHOPEE_PARTNER_ID,
    }
    params = f"partner_id={SHOPEE_PARTNER_ID}&timestamp={ts}&sign={sign}"
    r = requests.post(
        f"{SHOPEE_BASE_URL}{path}?{params}",
        json=body,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    return r.json()


def get_all_orders(access_token, shop_id, days=15):
    now = int(time.time())
    time_from = now - (days * 86400)
    all_sns = []
    cursor = ""
    while True:
        params = dict(
            time_range_field="create_time",
            time_from=time_from,
            time_to=now,
            page_size=100,
        )
        if cursor:
            params["cursor"] = cursor
        r = shopee_get("/api/v2/order/get_order_list", access_token, shop_id, params)
        if r.get("error"):
            print(f"  Erro ao buscar pedidos: {r['error']} — {r.get('message')}")
            break
        resp = r.get("response", {})
        all_sns += [o["order_sn"] for o in resp.get("order_list", [])]
        if not resp.get("more"):
            break
        cursor = resp["next_cursor"]
    return all_sns


def get_order_details(access_token, shop_id, sns):
    details = []
    for i in range(0, len(sns), 50):
        batch = sns[i:i+50]
        r = shopee_get("/api/v2/order/get_order_detail", access_token, shop_id, {
            "order_sn_list": ",".join(batch),
            "response_optional_fields": "order_status,total_amount,cancel_reason,item_list",
        })
        if r.get("response", {}).get("order_list"):
            details += r["response"]["order_list"]
    return details


def calcular_metricas(details, nome_loja):
    PAGOS = {"READY_TO_SHIP", "PROCESSED", "SHIPPED", "TO_CONFIRM_RECEIVE", "COMPLETED", "IN_CANCEL"}
    CANCELADOS = {"CANCELLED"}

    pedidos_pagos, pedidos_cancelados = 0, 0
    faturamento = 0.0
    cancel_reasons = {}

    for o in details:
        s = o.get("order_status", "")
        amt = float(o.get("total_amount", 0))
        if s in PAGOS:
            pedidos_pagos += 1
            faturamento += amt
        if s in CANCELADOS:
            pedidos_cancelados += 1
            r = o.get("cancel_reason") or "sem motivo"
            cancel_reasons[r] = cancel_reasons.get(r, 0) + 1

    total = len(details)
    ticket_medio = faturamento / pedidos_pagos if pedidos_pagos else 0
    taxa_cancel = pedidos_cancelados / total * 100 if total else 0

    if taxa_cancel >= 20:
        alerta_base = "CRÍTICO"
    elif taxa_cancel >= 10:
        alerta_base = "ATENÇÃO"
    else:
        alerta_base = "OK"

    return {
        "nome": nome_loja,
        "total_pedidos": total,
        "pedidos_pagos": pedidos_pagos,
        "pedidos_cancelados": pedidos_cancelados,
        "faturamento": round(faturamento, 2),
        "ticket_medio": round(ticket_medio, 2),
        "taxa_cancelamento": round(taxa_cancel, 1),
        "alerta_base": alerta_base,
        "cancel_reasons": cancel_reasons,
    }

# ─── CLAUDE API ───────────────────────────────────────────────

def gerar_diagnostico(metricas):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""Você é o Claudin, assistente especialista em análise de lojas Shopee brasileiras.
Analise os dados abaixo e gere um diagnóstico objetivo em português do Brasil.

LOJA: {metricas['nome'].upper()}
PERÍODO: 15 dias
MÉTRICAS:
- Total de pedidos: {metricas['total_pedidos']}
- Pedidos pagos/em andamento: {metricas['pedidos_pagos']}
- Pedidos cancelados: {metricas['pedidos_cancelados']} ({metricas['taxa_cancelamento']}%)
- Faturamento pago: R${metricas['faturamento']:,.2f}
- Ticket médio: R${metricas['ticket_medio']:.2f}
- Motivos de cancelamento: {json.dumps(metricas['cancel_reasons'], ensure_ascii=False)}

CRITÉRIOS DE ALERTA:
- CRÍTICO: cancelamentos > 20%, conversão < 0.5%, ROAS < 1x
- ATENÇÃO: cancelamentos 10-20%, conversão 0.5-1%, ROAS 1-3x
- OK: tudo dentro do esperado

Responda SOMENTE com JSON válido, sem markdown:
{{
  "alerta": "CRÍTICO|ATENÇÃO|OK",
  "problema1": "descrição com números reais do dado mais crítico",
  "problema2": "descrição do segundo problema mais importante",
  "acao_urgente": "ação específica e prática para fazer hoje",
  "diagnostico_completo": "diagnóstico de 3 parágrafos para documentar no Monday. Seja específico com os números."
}}"""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

# ─── MONDAY API ───────────────────────────────────────────────

def atualizar_monday(monday_item_id, metricas, diagnostico):
    if not MONDAY_API_KEY or not monday_item_id:
        print("  Monday não configurado — pulando atualização")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    alerta = diagnostico["alerta"]

    col_values = json.dumps({
        "color_mm3zken8":    {"label": alerta},
        "numeric_mm3zpz0y":  metricas["taxa_cancelamento"],
        "numeric_mm3ztn":    metricas["ticket_medio"],
        "date_mm3zvmyj":     {"date": today},
    })

    query = """
    mutation ($itemId: ID!, $boardId: ID!, $colVals: JSON!) {
      change_multiple_column_values(
        item_id: $itemId,
        board_id: $boardId,
        column_values: $colVals,
        create_labels_if_missing: true
      ) { id }
    }
    """
    r = requests.post(
        "https://api.monday.com/v2",
        headers={"Authorization": MONDAY_API_KEY, "Content-Type": "application/json"},
        json={
            "query": query,
            "variables": {
                "itemId": str(monday_item_id),
                "boardId": str(MONDAY_BOARD_ID),
                "colVals": col_values,
            },
        },
        timeout=15,
    )
    print(f"  Monday atualizado: {r.status_code}")

# ─── ANÁLISE PRINCIPAL ────────────────────────────────────────

def analisar_loja(nome, shop_id, access_token, monday_id=None):
    print(f"\n{'='*50}")
    print(f"📊 Analisando: {nome.upper()}")
    print(f"{'='*50}")

    print("  → Buscando pedidos (15 dias)...")
    sns = get_all_orders(access_token, shop_id)
    print(f"  → {len(sns)} pedidos encontrados")

    if not sns:
        print("  Sem pedidos no período — pulando")
        return None

    print("  → Buscando detalhes...")
    details = get_order_details(access_token, shop_id, sns)
    print(f"  → {len(details)} detalhes obtidos")

    metricas = calcular_metricas(details, nome)

    print(f"\n  Faturamento:   R${metricas['faturamento']:,.2f}")
    print(f"  Pedidos pagos: {metricas['pedidos_pagos']}")
    print(f"  Cancelamentos: {metricas['pedidos_cancelados']} ({metricas['taxa_cancelamento']}%)")
    print(f"  Ticket médio:  R${metricas['ticket_medio']:.2f}")

    print("\n  → Gerando diagnóstico com Claude...")
    diagnostico = gerar_diagnostico(metricas)

    print(f"\n  🚨 ALERTA: {diagnostico['alerta']}")
    print(f"  Problema 1:   {diagnostico['problema1']}")
    print(f"  Problema 2:   {diagnostico['problema2']}")
    print(f"  Ação urgente: {diagnostico['acao_urgente']}")

    if monday_id:
        print("\n  → Atualizando Monday...")
        atualizar_monday(monday_id, metricas, diagnostico)

    return {"metricas": metricas, "diagnostico": diagnostico}


def gerar_link_autorizacao():
    ts = int(time.time())
    path = "/api/v2/shop/auth_partner"
    sign = _sign(path, ts)
    return (
        f"{SHOPEE_BASE_URL}{path}"
        f"?partner_id={SHOPEE_PARTNER_ID}"
        f"&timestamp={ts}&sign={sign}"
        f"&redirect=https://analiseup.com.br/callback"
    )

# ─── ENTRY POINT ─────────────────────────────────────────────

def main():
    print(f"\n🤖 AnáliseUp Agent — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("=" * 50)

    tokens = carregar_tokens()

    resultados = {}
    for nome, cfg in CLIENTES.items():
        token_data = tokens.get(nome, {})
        shop_id = cfg.get("shop_id") or token_data.get("shop_id")
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")

        if not shop_id or not access_token:
            print(f"⏭  {nome}: sem token — pulando")
            continue

        # Tentar renovar token se antigo (> 3 horas)
        atualizado_em = token_data.get("atualizado_em", 0)
        if time.time() - atualizado_em > 10800 and refresh_token:
            print(f"  🔄 Renovando token de {nome}...")
            novo = renovar_token(refresh_token, shop_id)
            if novo.get("access_token"):
                access_token = novo["access_token"]
                salvar_tokens(nome, shop_id, access_token, novo.get("refresh_token", refresh_token))

        result = analisar_loja(nome, shop_id, access_token, cfg.get("monday_id"))
        if result:
            resultados[nome] = result

    if not resultados:
        print("\n⚠️  Nenhuma loja com token configurado.")
        print("Execute localmente: python3 analiseup_agent.py auth")
    else:
        print(f"\n\n✅ Análise concluída para {len(resultados)} loja(s)")
        for nome, r in resultados.items():
            print(f"   {nome}: {r['diagnostico']['alerta']} — {r['metricas']['faturamento']:,.2f} BRL")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "auth":
        print("\n📎 Link de autorização (enviar para o cliente):")
        print(gerar_link_autorizacao())
    else:
        main()
