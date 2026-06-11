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
from datetime import datetime, timedelta

# ─── CONFIGURAÇÕES (via env vars) ────────────────────────────
SHOPEE_PARTNER_ID   = int(os.environ.get("SHOPEE_PARTNER_ID", "2036153"))
SHOPEE_PARTNER_KEY  = os.environ.get("SHOPEE_PARTNER_KEY", "")  # definir via env (Railway) ou rodar_local.sh
ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
MONDAY_API_KEY      = os.environ.get("MONDAY_API_KEY", "")
MONDAY_BOARD_ID     = int(os.environ.get("MONDAY_BOARD_ID", "9943771778"))
SHOPEE_BASE_URL     = "https://partner.shopeemobile.com"
RAILWAY_URL         = os.environ.get("RAILWAY_URL", "https://analiseup.com.br")
CLAUDIN_API_SECRET  = os.environ.get("CLAUDIN_API_SECRET", "")  # definir via env (Railway) ou rodar_local.sh

# Board ANALISEUP — métricas de performance (ROAS, pedidos, situação)
ANALISEUP_BOARD_ID  = int(os.environ.get("ANALISEUP_BOARD_ID", "18394145812"))

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

# Itens no board ANALISEUP (18394145812) — mapeamento nome → item_id
ANALISEUP_IDS = {
    "aorbe":           12247929610,
    "almavoga":        12247999349,
    "marcos_republik": 12247943762,
    "rosaliz":         12247980019,
    "kboutique":       12247941641,
}

# ─── TOKENS ──────────────────────────────────────────────────

def carregar_tokens_php():
    """
    Lê shopee_tokens.json (formato PHP/callback) e converte para o formato
    Python (indexado por nome do cliente). Funciona quando rodando no Railway.
    """
    shop_to_nome = {
        cfg["shop_id"]: nome
        for nome, cfg in CLIENTES.items()
        if cfg.get("shop_id")
    }
    # Tenta ler da pasta do script primeiro, depois da pasta atual
    for caminho in ["shopee_tokens.json", "../shopee_tokens.json"]:
        if os.path.exists(caminho):
            try:
                with open(caminho) as f:
                    tokens_php = json.load(f)
                tokens_convertidos = {}
                for shop_id_str, data in tokens_php.items():
                    shop_id = int(shop_id_str)
                    nome = shop_to_nome.get(shop_id)
                    if nome:
                        tokens_convertidos[nome] = {
                            "shop_id":       shop_id,
                            "access_token":  data["access_token"],
                            "refresh_token": data.get("refresh_token", ""),
                            "atualizado_em": int(time.time()),
                        }
                if tokens_convertidos:
                    print(f"✅ Tokens lidos de {caminho}: {len(tokens_convertidos)} lojas")
                    return tokens_convertidos
            except Exception as e:
                print(f"⚠️  Erro ao ler {caminho}: {e}")
    return {}


def sincronizar_tokens_railway():
    """
    Busca tokens frescos do servidor Railway (analiseup.com.br/api/tokens.php)
    e os salva em tokens.json, mapeando shop_id → nome do cliente.
    Chamado automaticamente antes de cada análise.
    """
    # Monta mapa inverso: shop_id → nome
    shop_to_nome = {
        cfg["shop_id"]: nome
        for nome, cfg in CLIENTES.items()
        if cfg.get("shop_id")
    }

    url = f"{RAILWAY_URL}/api/tokens.php?key={CLAUDIN_API_SECRET}"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 401:
            print("⚠️  Chave do endpoint inválida — tokens não sincronizados")
            return
        data = r.json()
    except Exception as e:
        print(f"⚠️  Não foi possível buscar tokens do Railway: {e}")
        return

    tokens_railway = data.get("tokens", {})
    if not tokens_railway:
        print("⚠️  Nenhum token retornado pelo Railway")
        return

    # Carrega tokens locais existentes para não sobrescrever dados extras
    try:
        with open("tokens.json") as f:
            tokens_local = json.load(f)
    except FileNotFoundError:
        tokens_local = {}

    atualizados = 0
    for shop_id_str, token_data in tokens_railway.items():
        shop_id = int(shop_id_str)
        nome = shop_to_nome.get(shop_id)
        if not nome:
            continue  # shop_id não mapeado ainda
        tokens_local[nome] = {
            "shop_id":       shop_id,
            "access_token":  token_data["access_token"],
            "refresh_token": token_data.get("refresh_token", ""),
            "atualizado_em": int(time.time()),
        }
        atualizados += 1

    with open("tokens.json", "w") as f:
        json.dump(tokens_local, f, indent=2)

    renovados = data.get("renovados", 0)
    print(f"✅ Tokens sincronizados: {atualizados} lojas"
          + (f" ({renovados} renovados no servidor)" if renovados else ""))


def carregar_tokens():
    """
    Tenta carregar tokens de 4 fontes (em ordem de prioridade):
    1. shopee_tokens.json (formato PHP — disponível no Railway)
    2. Sincroniza com Railway via HTTP (disponível localmente)
    3. Variável de ambiente TOKENS_JSON
    4. Arquivo tokens.json local
    """
    # 1. shopee_tokens.json (quando rodando no próprio Railway)
    tokens_php = carregar_tokens_php()
    if tokens_php:
        return tokens_php

    # 2. Sincroniza via HTTP (quando rodando fora do Railway)
    sync_ok = False
    try:
        sincronizar_tokens_railway()
        sync_ok = True
    except Exception as e:
        print(f"⚠️  Sync Railway falhou: {e}")

    # 3. Arquivo tokens.json local (atualizado pela sincronização acima)
    if sync_ok:
        try:
            with open("tokens.json") as f:
                tokens_local = json.load(f)
            if tokens_local:
                return tokens_local
        except FileNotFoundError:
            pass

    # 4. Variável de ambiente (fallback — só usado se sync falhar)
    tokens_env = os.environ.get("TOKENS_JSON", "")
    if tokens_env:
        try:
            return json.loads(tokens_env)
        except json.JSONDecodeError:
            print("⚠️  TOKENS_JSON inválido — ignorando")

    # 5. Arquivo tokens.json local (último recurso)
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


def get_all_orders_range(access_token, shop_id, time_from, time_to):
    """Busca order_sn's no intervalo [time_from, time_to] (máx. 15 dias, limite da API Shopee)."""
    all_sns = []
    cursor = ""
    while True:
        params = dict(
            time_range_field="create_time",
            time_from=time_from,
            time_to=time_to,
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


def get_all_orders(access_token, shop_id, days=15):
    """Busca pedidos dos últimos `days` dias. A API da Shopee limita cada
    chamada a uma janela de no máximo 15 dias, então quebramos em blocos."""
    now = int(time.time())
    all_sns = []
    restante = days
    janela_fim = now
    while restante > 0:
        bloco = min(restante, 15)
        janela_inicio = janela_fim - (bloco * 86400)
        all_sns += get_all_orders_range(access_token, shop_id, janela_inicio, janela_fim)
        janela_fim = janela_inicio
        restante -= bloco
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


def get_item_list(access_token, shop_id):
    """Lista todos os produtos ativos (status NORMAL) da loja."""
    items = []
    offset = 0
    while True:
        params = dict(offset=offset, page_size=100, item_status="NORMAL")
        r = shopee_get("/api/v2/product/get_item_list", access_token, shop_id, params)
        resp = r.get("response", {})
        batch = resp.get("item", [])
        if not batch:
            break
        items += batch
        if not resp.get("has_next_page"):
            break
        offset += 100
    return items


def get_item_base_info(access_token, shop_id, item_ids):
    """Detalhes (estoque, preço, nome) dos produtos, em lotes de 50."""
    details = []
    for i in range(0, len(item_ids), 50):
        chunk = item_ids[i:i + 50]
        r = shopee_get("/api/v2/product/get_item_base_info", access_token, shop_id, {
            "item_id_list": ",".join(str(x) for x in chunk),
            "need_tax_info": "false",
            "need_complaint_policy": "false",
            "response_optional_fields": "stock_info_v2,price_info,item_status",
        })
        if r.get("error"):
            print(f"  ⚠️  get_item_base_info erro: {r['error']} — {r.get('message')}")
        resp = r.get("response", {})
        details += resp.get("item_list", [])
    return details


def analisar_produtos(access_token, shop_id):
    """
    Verifica produtos ativos com estoque zerado ou crítico — produtos com
    potencial de venda que estão fora do ar por falta de estoque.
    """
    items = get_item_list(access_token, shop_id)
    item_ids = [i["item_id"] for i in items if "item_id" in i]

    if not item_ids:
        return {"total_produtos": 0, "sem_estoque": [], "estoque_baixo": []}

    details = get_item_base_info(access_token, shop_id, item_ids)

    sem_estoque = []
    estoque_baixo = []
    sem_dado_estoque = 0
    for d in details:
        nome = d.get("item_name", "?")

        stock_info = d.get("stock_info_v2")
        if stock_info and "seller_stock" in stock_info:
            stock_list = stock_info.get("seller_stock") or []
            total_stock = sum(s.get("stock", 0) for s in stock_list)
        elif "stock_info" in d:
            stock_list = d.get("stock_info") or []
            total_stock = sum(s.get("current_stock", 0) for s in stock_list)
        else:
            sem_dado_estoque += 1
            continue

        if total_stock == 0:
            sem_estoque.append(nome)
        elif total_stock <= 3:
            estoque_baixo.append({"nome": nome, "estoque": total_stock})

    return {
        "total_produtos": len(details),
        "sem_estoque": sem_estoque,
        "estoque_baixo": estoque_baixo,
        "sem_dado_estoque": sem_dado_estoque,
    }


def get_shop_performance(access_token, shop_id):
    r = shopee_get("/api/v2/shop/get_shop_performance", access_token, shop_id)
    if r.get("error"):
        print(f"  ⚠️  get_shop_performance erro: {r['error']} — {r.get('message')}")
        return {}
    return r.get("response", {})


def analisar_saude_loja(access_token, shop_id):
    """
    Coleta indicadores de saúde da loja: avaliação, penalidades,
    atraso no envio, tempo de resposta ao chat, etc.
    Retorna o bloco bruto relevante — a interpretação fica a cargo do Claude
    no diagnóstico, já que os nomes dos indicadores variam por região/conta.
    """
    perf = get_shop_performance(access_token, shop_id)
    if not perf:
        return None

    overall = perf.get("overall_performance", {})
    rating = overall.get("rating", {})
    penalty = perf.get("penalty", {})
    metric_list = perf.get("metric_list", [])

    metricas = []
    for m in metric_list:
        metricas.append({
            "nome": m.get("metric_name") or m.get("metric_id"),
            "valor_atual": m.get("current_period"),
            "meta": m.get("target"),
        })

    return {
        "avaliacao_media": rating.get("average_star"),
        "total_avaliacoes": rating.get("total_rating_count"),
        "pontos_penalidade": penalty.get("total_penalty_points") or penalty.get("penalty_points"),
        "punicoes_ativas": penalty.get("punishment_list") or penalty.get("punishments"),
        "indicadores": metricas,
    }


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

def calcular_pedidos_30dias(access_token, shop_id):
    """
    Conta pedidos pagos/em andamento nos últimos 30 dias e calcula a média
    diária de pedidos. Reaproveita get_all_orders / get_order_details.
    """
    PAGOS = {"READY_TO_SHIP", "PROCESSED", "SHIPPED", "TO_CONFIRM_RECEIVE", "COMPLETED", "IN_CANCEL"}

    sns = get_all_orders(access_token, shop_id, days=30)
    if not sns:
        return {"pedidos_30_dias": 0, "media_diaria": 0.0}

    details = get_order_details(access_token, shop_id, sns)
    pedidos_pagos = sum(1 for o in details if o.get("order_status", "") in PAGOS)

    return {
        "pedidos_30_dias": pedidos_pagos,
        "media_diaria": round(pedidos_pagos / 30, 1),
    }


# ─── SHOPEE ADS API ───────────────────────────────────────────

def _data_ads(dias_atras=0):
    """Data no formato DD-MM-YYYY exigido pela Ads API."""
    return (datetime.now() - timedelta(days=dias_atras)).strftime("%d-%m-%Y")


def get_ads_saldo(access_token, shop_id):
    """Saldo de créditos de Ads em tempo real."""
    r = shopee_get("/api/v2/ads/get_total_balance", access_token, shop_id)
    if r.get("error"):
        print(f"  ⚠️  ads saldo erro: {r['error']} — {r.get('message')}")
        return None
    return r.get("response", {}).get("total_balance")


def get_ads_performance_diaria(access_token, shop_id, dias=30):
    """
    Performance diária de Ads no nível da loja (CTR, cliques, gasto, GMV, ROAS).
    Máx. 30 dias por chamada (limite da API: intervalo de até 1 mês).
    """
    params = {"start_date": _data_ads(dias - 1), "end_date": _data_ads(0)}
    r = shopee_get("/api/v2/ads/get_all_cpc_ads_daily_performance", access_token, shop_id, params)
    if r.get("error"):
        print(f"  ⚠️  ads performance diária erro: {r['error']} — {r.get('message')}")
        return None
    return r.get("response") or []


def get_campanhas_ids(access_token, shop_id):
    """Lista todos os campaign_ids de Product Ads da loja."""
    ids = []
    offset = 0
    while True:
        r = shopee_get("/api/v2/ads/get_product_level_campaign_id_list", access_token, shop_id,
                       {"ad_type": "all", "offset": offset, "limit": 100})
        if r.get("error"):
            print(f"  ⚠️  ads campanhas erro: {r['error']} — {r.get('message')}")
            break
        resp = r.get("response", {})
        batch = resp.get("campaign_list") or []
        ids += [c["campaign_id"] for c in batch if "campaign_id" in c]
        if not resp.get("has_next_page") or not batch:
            break
        offset += len(batch)
    return ids


def get_campanhas_config(access_token, shop_id, campaign_ids):
    """Configurações das campanhas (nome, status, budget, ROAS target). Lotes de 100."""
    configs = []
    for i in range(0, len(campaign_ids), 100):
        chunk = campaign_ids[i:i + 100]
        r = shopee_get("/api/v2/ads/get_product_level_campaign_setting_info", access_token, shop_id, {
            "info_type_list": "1,3",
            "campaign_id_list": ",".join(str(c) for c in chunk),
        })
        if r.get("error"):
            print(f"  ⚠️  ads config erro: {r['error']} — {r.get('message')}")
            continue
        configs += r.get("response", {}).get("campaign_list") or []
    return configs


def get_campanhas_performance(access_token, shop_id, campaign_ids, dias=7):
    """Performance diária por campanha nos últimos `dias` dias. Lotes de 100."""
    perfs = []
    params_data = {"start_date": _data_ads(dias - 1), "end_date": _data_ads(0)}
    for i in range(0, len(campaign_ids), 100):
        chunk = campaign_ids[i:i + 100]
        r = shopee_get("/api/v2/ads/get_product_campaign_daily_performance", access_token, shop_id, {
            **params_data,
            "campaign_id_list": ",".join(str(c) for c in chunk),
        })
        if r.get("error"):
            print(f"  ⚠️  ads perf campanha erro: {r['error']} — {r.get('message')}")
            continue
        perfs += r.get("response", {}).get("campaign_list") or []
    return perfs


def analisar_ads(access_token, shop_id):
    """
    Análise completa de Shopee Ads:
    - ROAS/gasto/GMV de 7 e 30 dias (nível loja)
    - CTR, CPC e conversão médios
    - Saldo de créditos
    - Status das campanhas + performance individual (7 dias)
    - Alertas automáticos (verba queimando, ads parados, saldo zerado, etc.)
    Retorna None se a loja não tiver acesso à Ads API.
    """
    diaria = get_ads_performance_diaria(access_token, shop_id, dias=30)
    if diaria is None:
        return None

    def soma(registros, campo):
        return sum(float(d.get(campo) or 0) for d in registros)

    ultimos7 = diaria[-7:] if len(diaria) >= 7 else diaria

    gasto_30, gmv_30 = soma(diaria, "expense"), soma(diaria, "broad_gmv")
    gasto_7,  gmv_7  = soma(ultimos7, "expense"), soma(ultimos7, "broad_gmv")
    cliques_30 = soma(diaria, "clicks")
    impressoes_30 = soma(diaria, "impression")
    pedidos_ads_30 = soma(diaria, "broad_order")

    roas_30 = round(gmv_30 / gasto_30, 2) if gasto_30 else None
    roas_7  = round(gmv_7 / gasto_7, 2) if gasto_7 else None
    ctr_30  = round(cliques_30 / impressoes_30 * 100, 2) if impressoes_30 else 0
    cpc_30  = round(gasto_30 / cliques_30, 2) if cliques_30 else 0
    conversao_30 = round(pedidos_ads_30 / cliques_30 * 100, 2) if cliques_30 else 0
    dias_sem_veicular_7 = sum(1 for d in ultimos7 if float(d.get("expense") or 0) == 0)

    saldo = get_ads_saldo(access_token, shop_id)

    # ── Campanhas ──
    campanhas = []
    ativas = pausadas = 0
    try:
        ids = get_campanhas_ids(access_token, shop_id)
        if ids:
            configs = {c["campaign_id"]: c for c in get_campanhas_config(access_token, shop_id, ids)}
            perfs = get_campanhas_performance(access_token, shop_id, ids, dias=7)
            for p in perfs:
                cid = p.get("campaign_id")
                cfg = configs.get(cid, {})
                common = cfg.get("common_info") or {}
                auto_bid = cfg.get("auto_bidding_info") or {}
                m = p.get("metrics_list") or []
                gasto = sum(float(x.get("expense") or 0) for x in m)
                gmv = sum(float(x.get("broad_gmv") or 0) for x in m)
                cliques = sum(float(x.get("clicks") or 0) for x in m)
                impr = sum(float(x.get("impression") or 0) for x in m)
                pedidos = sum(float(x.get("broad_order") or 0) for x in m)
                status = common.get("campaign_status", "?")
                if status == "ongoing":
                    ativas += 1
                elif status == "paused":
                    pausadas += 1
                campanhas.append({
                    "nome": (p.get("ad_name") or common.get("ad_name") or "?")[:80],
                    "status": status,
                    "budget_diario": common.get("campaign_budget"),
                    "roas_target": auto_bid.get("roas_target"),
                    "gasto_7d": round(gasto, 2),
                    "gmv_7d": round(gmv, 2),
                    "roas_7d": round(gmv / gasto, 2) if gasto else None,
                    "ctr_7d": round(cliques / impr * 100, 2) if impr else 0,
                    "cpc_7d": round(gasto / cliques, 2) if cliques else 0,
                    "pedidos_7d": int(pedidos),
                })
    except Exception as e:
        print(f"  ⚠️  Erro ao buscar campanhas: {e}")

    # ── Alertas automáticos ──
    alertas = []
    if saldo is not None and saldo <= 0 and ativas > 0:
        alertas.append(f"SALDO ZERADO (R${saldo}) com {ativas} campanha(s) ativa(s) — anúncios podem parar/já pararam de veicular")
    if dias_sem_veicular_7 >= 3:
        alertas.append(f"{dias_sem_veicular_7} dos últimos 7 dias SEM veiculação de ads (gasto R$0) — loja perdendo tráfego pago")
    if roas_7 is not None and roas_7 < 1:
        alertas.append(f"ROAS 7 dias de {roas_7}x — abaixo de 1x, ads queimando dinheiro (gastou R${gasto_7:.2f} para vender R${gmv_7:.2f})")
    elif roas_7 is not None and roas_7 < 3:
        alertas.append(f"ROAS 7 dias de {roas_7}x — provavelmente abaixo do breakeven, revisar margem vs. custo de ads")
    if ativas == 0 and campanhas:
        alertas.append(f"NENHUMA campanha ativa ({pausadas} pausadas de {len(campanhas)} totais) — loja sem tráfego pago")
    if ctr_30 and ctr_30 < 1 and impressoes_30 > 1000:
        alertas.append(f"CTR médio de {ctr_30}% (abaixo de 1%) — imagem de capa/título/preço pouco atrativos no anúncio")
    for c in campanhas:
        if c["status"] == "ongoing" and c["gasto_7d"] >= 10 and (c["roas_7d"] or 0) < 1:
            alertas.append(f"Campanha '{c['nome']}' gastou R${c['gasto_7d']:.2f} em 7d com ROAS {c['roas_7d'] or 0}x — pausar ou reotimizar")
        if c["status"] == "ongoing" and c["roas_target"] and c["roas_7d"] and c["roas_7d"] < c["roas_target"] * 0.5:
            alertas.append(f"Campanha '{c['nome']}' com ROAS {c['roas_7d']}x muito abaixo do target {c['roas_target']}x")

    return {
        "saldo": saldo,
        "gasto_7": round(gasto_7, 2), "gmv_7": round(gmv_7, 2), "roas_7": roas_7,
        "gasto_30": round(gasto_30, 2), "gmv_30": round(gmv_30, 2), "roas_30": roas_30,
        "ctr_30": ctr_30, "cpc_30": cpc_30, "conversao_30": conversao_30,
        "impressoes_30": int(impressoes_30), "cliques_30": int(cliques_30),
        "pedidos_ads_30": int(pedidos_ads_30),
        "dias_sem_veicular_7": dias_sem_veicular_7,
        "campanhas_total": len(campanhas), "campanhas_ativas": ativas, "campanhas_pausadas": pausadas,
        "campanhas": campanhas,
        "alertas": alertas,
    }


# ─── CLAUDE API ───────────────────────────────────────────────

def gerar_diagnostico(metricas, produtos=None, saude=None, ads=None):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    bloco_produtos = ""
    if produtos is not None and produtos["total_produtos"] > 0:
        if produtos.get("sem_dado_estoque") == produtos["total_produtos"]:
            bloco_produtos = f"""
PRODUTOS (catálogo ativo):
- Total de produtos ativos: {produtos['total_produtos']}
- Dados de estoque indisponíveis para esses produtos nesta execução (não considere estoque no diagnóstico).
"""
        else:
            bloco_produtos = f"""
PRODUTOS (catálogo ativo):
- Total de produtos ativos: {produtos['total_produtos']}
- Produtos com estoque ZERADO (fora do ar, sem poder vender): {len(produtos['sem_estoque'])} — {json.dumps(produtos['sem_estoque'][:10], ensure_ascii=False)}
- Produtos com estoque crítico (≤3 unidades): {len(produtos['estoque_baixo'])} — {json.dumps(produtos['estoque_baixo'][:10], ensure_ascii=False)}
"""
    elif produtos is not None and produtos["total_produtos"] == 0:
        bloco_produtos = "\nPRODUTOS: nenhum produto ativo encontrado no catálogo.\n"

    bloco_saude = ""
    if saude:
        bloco_saude = f"""
SAÚDE DA LOJA:
- Avaliação média: {saude.get('avaliacao_media')} ({saude.get('total_avaliacoes')} avaliações)
- Pontos de penalidade: {saude.get('pontos_penalidade')}
- Punições ativas: {json.dumps(saude.get('punicoes_ativas'), ensure_ascii=False)}
- Indicadores de performance (atraso no envio, tempo de resposta, etc.): {json.dumps(saude.get('indicadores'), ensure_ascii=False)}
"""
    elif saude is None:
        bloco_saude = "\nSAÚDE DA LOJA: dados indisponíveis nesta execução.\n"

    bloco_ads = ""
    if ads:
        top_campanhas = sorted(
            [c for c in ads["campanhas"] if c["status"] == "ongoing" or c["gasto_7d"] > 0],
            key=lambda c: c["gasto_7d"], reverse=True
        )[:8]
        bloco_ads = f"""
SHOPEE ADS:
- Saldo de créditos: {f"R${ads['saldo']:.2f}" if ads['saldo'] is not None else "indisponível"}
- Últimos 7 dias: gasto R${ads['gasto_7']:.2f} | GMV via ads R${ads['gmv_7']:.2f} | ROAS {ads['roas_7'] if ads['roas_7'] is not None else "sem gasto"}
- Últimos 30 dias: gasto R${ads['gasto_30']:.2f} | GMV via ads R${ads['gmv_30']:.2f} | ROAS {ads['roas_30'] if ads['roas_30'] is not None else "sem gasto"}
- CTR médio 30d: {ads['ctr_30']}% | CPC médio: R${ads['cpc_30']:.2f} | Conversão: {ads['conversao_30']}% | {ads['impressoes_30']} impressões, {ads['cliques_30']} cliques, {ads['pedidos_ads_30']} pedidos via ads
- Dias sem veiculação nos últimos 7: {ads['dias_sem_veicular_7']}
- Campanhas: {ads['campanhas_total']} no total | {ads['campanhas_ativas']} ativas | {ads['campanhas_pausadas']} pausadas
- Detalhe das campanhas relevantes (últimos 7 dias): {json.dumps(top_campanhas, ensure_ascii=False)}
- ALERTAS AUTOMÁTICOS DE ADS: {json.dumps(ads['alertas'], ensure_ascii=False)}
"""
    elif ads is None:
        bloco_ads = "\nSHOPEE ADS: dados indisponíveis nesta execução (sem acesso à Ads API ou erro na coleta).\n"

    prompt = f"""Você é o Claudin, assistente especialista em análise de lojas Shopee brasileiras.
Analise os dados abaixo e gere um diagnóstico objetivo em português do Brasil.

LOJA: {metricas['nome'].upper()}
PERÍODO: 15 dias
MÉTRICAS DE VENDAS:
- Total de pedidos: {metricas['total_pedidos']}
- Pedidos pagos/em andamento: {metricas['pedidos_pagos']}
- Pedidos cancelados: {metricas['pedidos_cancelados']} ({metricas['taxa_cancelamento']}%)
- Faturamento pago: R${metricas['faturamento']:,.2f}
- Ticket médio: R${metricas['ticket_medio']:.2f}
- Motivos de cancelamento: {json.dumps(metricas['cancel_reasons'], ensure_ascii=False)}
{bloco_produtos}{bloco_saude}{bloco_ads}
CRITÉRIOS DE ALERTA:
- CRÍTICO: cancelamentos > 20%, conversão < 0.5%, ROAS < 1x, saldo de ads zerado com campanha ativa, ads parados há 3+ dias, muitos produtos sem estoque, penalidades ativas graves
- ATENÇÃO: cancelamentos 10-20%, conversão 0.5-1%, ROAS 1-3x, CTR < 1%, campanha individual queimando verba, alguns produtos com estoque crítico
- OK: tudo dentro do esperado

Considere TODOS os blocos de dados (vendas, produtos, saúde da loja, SHOPEE ADS) ao montar os 2 problemas — priorize o que tiver maior impacto financeiro e seja mais fácil de corrigir.
Sobre Ads: ROAS abaixo de ~3x geralmente significa prejuízo após margem; saldo zerado ou dias sem veiculação derrubam o tráfego da loja inteira; CTR baixo indica problema de capa/título/preço; conversão baixa com cliques altos indica problema na página do produto (fotos, descrição, avaliações, frete). Cite campanhas pelo nome quando relevante.

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
        max_tokens=2500,  # 1024 truncava o JSON com o bloco de Ads e quebrava o parse
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

def atualizar_analiseup(item_id, pedidos_30, media_diaria, alerta, roas_7=None, roas_30=None):
    """
    Atualiza o board ANALISEUP (18394145812) com as métricas de performance
    da loja. ROAS 7/30 dias ainda dependem da API de Ads da Shopee — quando
    None, a coluna não é enviada (fica como está no Monday).
    """
    if not MONDAY_API_KEY or not item_id:
        print("  ANALISEUP: Monday não configurado — pulando atualização")
        return

    # SITUAÇAO usa os labels: ATENÇAO, BOA, CRITICO
    mapa_situacao = {"CRÍTICO": "CRITICO", "ATENÇÃO": "ATENÇAO", "OK": "BOA"}
    situacao = mapa_situacao.get(alerta, "ATENÇAO")

    today = datetime.now().strftime("%Y-%m-%d")

    valores = {
        "data": {"date": today},
        "numeric_mkzbkjy9": pedidos_30,
        "numeric_mkzbhtae": media_diaria,
        "color_mm471xbe": {"label": situacao},
    }
    if roas_7 is not None:
        valores["numeric_mkzb7n4q"] = roas_7
    if roas_30 is not None:
        valores["numeric_mkzbf3wq"] = roas_30

    col_values = json.dumps(valores)

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
                "itemId": str(item_id),
                "boardId": str(ANALISEUP_BOARD_ID),
                "colVals": col_values,
            },
        },
        timeout=15,
    )
    print(f"  ANALISEUP atualizado: {r.status_code} — {r.text[:200]}")


def gerar_relatorio_txt(nome, metricas, diagnostico, produtos, saude, pedidos30, ads=None):
    """Monta o relatório 'Solução Claudin' em texto, no padrão usado nos diagnósticos."""
    hoje = datetime.now().strftime("%d/%m/%Y")
    linhas = []
    linhas.append("🤖 SOLUÇÃO CLAUDIN — DIAGNÓSTICO DA LOJA")
    linhas.append(f"Loja: {nome.upper()}")
    linhas.append(f"Data da análise: {hoje}")
    linhas.append(f"Alerta: {diagnostico['alerta']}")
    linhas.append("")
    linhas.append("─" * 50)
    linhas.append("📊 MÉTRICAS DE VENDAS (últimos 15 dias)")
    linhas.append("─" * 50)
    linhas.append(f"Total de pedidos: {metricas['total_pedidos']}")
    linhas.append(f"Pedidos pagos: {metricas['pedidos_pagos']}")
    linhas.append(f"Cancelamentos: {metricas['pedidos_cancelados']} ({metricas['taxa_cancelamento']}%)")
    linhas.append(f"Faturamento: R${metricas['faturamento']:,.2f}")
    linhas.append(f"Ticket médio: R${metricas['ticket_medio']:.2f}")
    if metricas.get("cancel_reasons"):
        linhas.append(f"Motivos de cancelamento: {json.dumps(metricas['cancel_reasons'], ensure_ascii=False)}")

    if pedidos30:
        linhas.append("")
        linhas.append("─" * 50)
        linhas.append("📈 VOLUME (últimos 30 dias)")
        linhas.append("─" * 50)
        linhas.append(f"Pedidos pagos em 30 dias: {pedidos30['pedidos_30_dias']}")
        linhas.append(f"Média diária de pedidos: {pedidos30['media_diaria']}")

    if produtos and produtos.get("total_produtos"):
        linhas.append("")
        linhas.append("─" * 50)
        linhas.append("📦 PRODUTOS")
        linhas.append("─" * 50)
        linhas.append(f"Total de produtos ativos: {produtos['total_produtos']}")
        if produtos.get("sem_estoque"):
            linhas.append(f"Sem estoque ({len(produtos['sem_estoque'])}): {', '.join(produtos['sem_estoque'][:10])}")
        if produtos.get("estoque_baixo"):
            criticos = ", ".join(f"{p['nome']} ({p['estoque']} un)" for p in produtos['estoque_baixo'][:10])
            linhas.append(f"Estoque crítico ({len(produtos['estoque_baixo'])}): {criticos}")

    if saude:
        linhas.append("")
        linhas.append("─" * 50)
        linhas.append("⭐ SAÚDE DA LOJA")
        linhas.append("─" * 50)
        linhas.append(f"Avaliação média: {saude.get('avaliacao_media')} ({saude.get('total_avaliacoes')} avaliações)")
        linhas.append(f"Pontos de penalidade: {saude.get('pontos_penalidade')}")

    if ads:
        linhas.append("")
        linhas.append("─" * 50)
        linhas.append("📣 SHOPEE ADS")
        linhas.append("─" * 50)
        if ads.get("saldo") is not None:
            linhas.append(f"Saldo de créditos: R${ads['saldo']:.2f}")
        roas7 = f"{ads['roas_7']}x" if ads.get("roas_7") is not None else "—"
        roas30 = f"{ads['roas_30']}x" if ads.get("roas_30") is not None else "—"
        linhas.append(f"7 dias:  gasto R${ads['gasto_7']:.2f} | GMV R${ads['gmv_7']:.2f} | ROAS {roas7}")
        linhas.append(f"30 dias: gasto R${ads['gasto_30']:.2f} | GMV R${ads['gmv_30']:.2f} | ROAS {roas30}")
        linhas.append(f"CTR: {ads['ctr_30']}% | CPC: R${ads['cpc_30']:.2f} | Conversão: {ads['conversao_30']}% "
                      f"({ads['impressoes_30']} impressões / {ads['cliques_30']} cliques / {ads['pedidos_ads_30']} pedidos)")
        linhas.append(f"Campanhas: {ads['campanhas_total']} total | {ads['campanhas_ativas']} ativas | {ads['campanhas_pausadas']} pausadas")
        if ads.get("dias_sem_veicular_7"):
            linhas.append(f"Dias sem veiculação (últimos 7): {ads['dias_sem_veicular_7']}")
        relevantes = sorted(
            [c for c in ads.get("campanhas", []) if c["status"] == "ongoing" or c["gasto_7d"] > 0],
            key=lambda c: c["gasto_7d"], reverse=True
        )[:10]
        if relevantes:
            linhas.append("")
            linhas.append("Campanhas (últimos 7 dias):")
            for c in relevantes:
                roas_c = f"{c['roas_7d']}x" if c["roas_7d"] is not None else "—"
                target = f" (target {c['roas_target']}x)" if c.get("roas_target") else ""
                linhas.append(f"  • [{c['status']}] {c['nome']}")
                linhas.append(f"    gasto R${c['gasto_7d']:.2f} | GMV R${c['gmv_7d']:.2f} | ROAS {roas_c}{target} | "
                              f"CTR {c['ctr_7d']}% | CPC R${c['cpc_7d']:.2f} | {c['pedidos_7d']} pedidos")
        if ads.get("alertas"):
            linhas.append("")
            linhas.append("⚠️  Alertas de Ads:")
            for a in ads["alertas"]:
                linhas.append(f"  • {a}")

    linhas.append("")
    linhas.append("─" * 50)
    linhas.append("🚨 DIAGNÓSTICO")
    linhas.append("─" * 50)
    linhas.append(f"Problema 1: {diagnostico['problema1']}")
    linhas.append(f"Problema 2: {diagnostico['problema2']}")
    linhas.append(f"Ação urgente: {diagnostico['acao_urgente']}")
    linhas.append("")
    linhas.append("─" * 50)
    linhas.append("📝 ANÁLISE COMPLETA")
    linhas.append("─" * 50)
    linhas.append(diagnostico.get("diagnostico_completo", ""))
    linhas.append("")
    linhas.append("─" * 50)
    linhas.append("🤖 Claudin — Seu assistente de agência Shopee")

    return "\n".join(linhas)


def enviar_arquivo_analiseup(item_id, nome, conteudo_txt):
    """Sobe o relatório .txt para a coluna ARQUIVO (file_mm472b2v) no board ANALISEUP."""
    if not MONDAY_API_KEY or not item_id:
        print("  ANALISEUP: Monday não configurado — pulando upload de arquivo")
        return

    hoje = datetime.now().strftime("%Y-%m-%d")
    filename = f"diagnostico_{nome}_{hoje}.txt"

    query = (
        "mutation ($file: File!) { add_file_to_column "
        f'(item_id: {int(item_id)}, column_id: "file_mm472b2v", file: $file) {{ id }} }}'
    )

    r = requests.post(
        "https://api.monday.com/v2/file",
        headers={"Authorization": MONDAY_API_KEY},
        data={"query": query},
        files={"variables[file]": (filename, conteudo_txt.encode("utf-8"), "text/plain")},
        timeout=30,
    )
    print(f"  ANALISEUP arquivo: {r.status_code} — {r.text[:200]}")


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

    print("\n  → Buscando produtos...")
    try:
        produtos = analisar_produtos(access_token, shop_id)
        print(f"  → {produtos['total_produtos']} produtos ativos | "
              f"{len(produtos['sem_estoque'])} sem estoque | "
              f"{len(produtos['estoque_baixo'])} com estoque crítico | "
              f"{produtos.get('sem_dado_estoque', 0)} sem dado de estoque")
    except Exception as e:
        print(f"  ⚠️  Erro ao buscar produtos: {e}")
        produtos = None

    print("  → Buscando saúde da loja...")
    try:
        saude = analisar_saude_loja(access_token, shop_id)
        if saude:
            print(f"  → Avaliação: {saude.get('avaliacao_media')} | "
                  f"Pontos de penalidade: {saude.get('pontos_penalidade')}")
        else:
            print("  → Sem dados de performance retornados")
    except Exception as e:
        print(f"  ⚠️  Erro ao buscar saúde da loja: {e}")
        saude = None

    print("  → Buscando Shopee Ads...")
    try:
        ads = analisar_ads(access_token, shop_id)
        if ads:
            roas7 = f"{ads['roas_7']}x" if ads['roas_7'] is not None else "—"
            print(f"  → Ads: saldo R${ads['saldo']} | gasto 7d R${ads['gasto_7']:.2f} | "
                  f"ROAS 7d {roas7} | {ads['campanhas_ativas']} campanha(s) ativa(s) | "
                  f"{len(ads['alertas'])} alerta(s)")
            for a in ads["alertas"]:
                print(f"     ⚠️  {a}")
        else:
            print("  → Sem dados de Ads (loja sem acesso à Ads API ou sem histórico)")
    except Exception as e:
        print(f"  ⚠️  Erro ao buscar Ads: {e}")
        ads = None

    print("\n  → Gerando diagnóstico com Claude...")
    diagnostico = gerar_diagnostico(metricas, produtos, saude, ads)

    print(f"\n  🚨 ALERTA: {diagnostico['alerta']}")
    print(f"  Problema 1:   {diagnostico['problema1']}")
    print(f"  Problema 2:   {diagnostico['problema2']}")
    print(f"  Ação urgente: {diagnostico['acao_urgente']}")

    if monday_id:
        print("\n  → Atualizando Monday...")
        atualizar_monday(monday_id, metricas, diagnostico)

    print("\n  → Calculando pedidos dos últimos 30 dias...")
    try:
        pedidos30 = calcular_pedidos_30dias(access_token, shop_id)
        print(f"  → {pedidos30['pedidos_30_dias']} pedidos pagos | "
              f"média diária: {pedidos30['media_diaria']}")
    except Exception as e:
        print(f"  ⚠️  Erro ao calcular pedidos 30 dias: {e}")
        pedidos30 = None

    analiseup_id = ANALISEUP_IDS.get(nome)
    if analiseup_id and pedidos30:
        print("\n  → Atualizando board ANALISEUP...")
        atualizar_analiseup(
            analiseup_id,
            pedidos30["pedidos_30_dias"],
            pedidos30["media_diaria"],
            diagnostico["alerta"],
            roas_7=ads.get("roas_7") if ads else None,
            roas_30=ads.get("roas_30") if ads else None,
        )

        print("  → Gerando e enviando relatório detalhado...")
        try:
            relatorio = gerar_relatorio_txt(nome, metricas, diagnostico, produtos, saude, pedidos30, ads)
            enviar_arquivo_analiseup(analiseup_id, nome, relatorio)
        except Exception as e:
            print(f"  ⚠️  Erro ao gerar/enviar relatório: {e}")

    return {
        "metricas": metricas,
        "diagnostico": diagnostico,
        "produtos": produtos,
        "saude": saude,
        "pedidos30": pedidos30,
        "ads": ads,
    }


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

        # Uma loja com erro não pode derrubar a análise das demais
        try:
            result = analisar_loja(nome, shop_id, access_token, cfg.get("monday_id"))
            if result:
                resultados[nome] = result
        except Exception as e:
            print(f"  ❌ Erro ao analisar {nome}: {e} — seguindo para a próxima loja")

    if not resultados:
        print("\n⚠️  Nenhuma loja com token configurado.")
        print("Execute localmente: python3 analiseup_agent.py auth")
    else:
        print(f"\n\n✅ Análise concluída para {len(resultados)} loja(s)")
        for nome, r in resultados.items():
            print(f"   {nome}: {r['diagnostico']['alerta']} — {r['metricas']['faturamento']:,.2f} BRL")


def debug_loja(nome):
    """Imprime as respostas brutas da Shopee para inspecionar nomes de campos."""
    tokens = carregar_tokens()
    cfg = CLIENTES.get(nome, {})
    token_data = tokens.get(nome, {})
    shop_id = cfg.get("shop_id") or token_data.get("shop_id")
    access_token = token_data.get("access_token")

    if not shop_id or not access_token:
        print(f"sem token para {nome}")
        return

    print("=== get_item_list (1 item) ===")
    r = shopee_get("/api/v2/product/get_item_list", access_token, shop_id,
                    dict(offset=0, page_size=1, item_status="NORMAL"))
    print(json.dumps(r, indent=2, ensure_ascii=False))

    item_ids = [i["item_id"] for i in r.get("response", {}).get("item", []) if "item_id" in i]
    if item_ids:
        print("\n=== get_item_base_info (1 item) ===")
        r2 = shopee_get("/api/v2/product/get_item_base_info", access_token, shop_id, {
            "item_id_list": ",".join(str(x) for x in item_ids),
            "need_tax_info": "false",
            "need_complaint_policy": "false",
        })
        print(json.dumps(r2, indent=2, ensure_ascii=False))

    print("\n=== get_shop_performance ===")
    r3 = shopee_get("/api/v2/shop/get_shop_performance", access_token, shop_id)
    print(json.dumps(r3, indent=2, ensure_ascii=False))


def testar_loja(nome):
    """Roda a análise completa para UMA loja só (teste do board ANALISEUP)."""
    tokens = carregar_tokens()
    cfg = CLIENTES.get(nome, {})
    token_data = tokens.get(nome, {})
    shop_id = cfg.get("shop_id") or token_data.get("shop_id")
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")

    if not shop_id or not access_token:
        print(f"sem token para {nome}")
        return

    atualizado_em = token_data.get("atualizado_em", 0)
    if time.time() - atualizado_em > 10800 and refresh_token:
        print(f"  🔄 Renovando token de {nome}...")
        novo = renovar_token(refresh_token, shop_id)
        if novo.get("access_token"):
            access_token = novo["access_token"]
            salvar_tokens(nome, shop_id, access_token, novo.get("refresh_token", refresh_token))

    analisar_loja(nome, shop_id, access_token, cfg.get("monday_id"))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "auth":
        print("\n📎 Link de autorização (enviar para o cliente):")
        print(gerar_link_autorizacao())
    elif len(sys.argv) > 2 and sys.argv[1] == "debug":
        debug_loja(sys.argv[2])
    elif len(sys.argv) > 2 and sys.argv[1] == "testar":
        testar_loja(sys.argv[2])
    else:
        main()
